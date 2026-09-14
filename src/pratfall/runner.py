import errno
import os
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from pratfall.codes import (
    EXECUTABLE_NOT_EXECUTABLE,
    EXECUTABLE_NOT_FOUND,
    PROCESS_IO_ERROR,
    STDERR_LIMIT_EXCEEDED,
    STDOUT_LIMIT_EXCEEDED,
    Code,
)
from pratfall.consumer import ByteConsumer, ConsumerFailure
from pratfall.interruption import (
    Handler,
    InterruptionState,
    Previous,
    handler_for,
    install,
    restore,
)
from pratfall.limits import FINAL_DRAIN_GRACE, STDERR_BYTES, STDOUT_BYTES, TERMINATE_GRACE
from pratfall.models import (
    Activity,
    Capture,
    ConsumedCapture,
    DecodedOutput,
    Invocation,
    RawCapture,
    ResultError,
)

READ_SIZE = 64 * 1024
PROGRESS_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 5.0


@dataclass(frozen=True)
class ProcessResult:
    capture: Capture
    stderr: bytes
    native_exit_code: int | None
    duration_ms: int
    error: ResultError | None = None
    timed_out: bool = False
    interrupted_by: int | None = None
    process_group: int | None = None


def raw_stdout(capture: Capture) -> bytes:
    if not isinstance(capture, RawCapture):
        raise TypeError("raw stdout is unavailable for a consumed capture")
    return capture.stdout


@dataclass(frozen=True)
class OutputLimits:
    stdout: int = STDOUT_BYTES
    stderr: int = STDERR_BYTES


DEFAULT_OUTPUT_LIMITS = OutputLimits()


@dataclass
class _SignalState(InterruptionState):
    process_group: int | None = None


@dataclass(frozen=True)
class _RunContext:
    started: float
    timeout: float
    signal_state: _SignalState
    output_limits: OutputLimits
    progress: Callable[[int, Activity], None] | None


def run(
    invocation: Invocation,
    cwd: Path,
    timeout: float,
    *,
    output_limits: OutputLimits = DEFAULT_OUTPUT_LIMITS,
    consumer: ByteConsumer | None = None,
    progress: Callable[[int, Activity], None] | None = None,
) -> ProcessResult:
    started = time.monotonic()
    consumer_state = _ConsumerState(consumer)
    if os.name != "posix":
        return _startup_failure(
            started, "unsupported_platform", "Process execution requires POSIX."
        )
    signal_state = _SignalState()
    previous: Previous = {}
    install(_interrupt_handler(signal_state), previous)
    process: subprocess.Popen[bytes] | None = None
    try:
        try:
            with tempfile.TemporaryFile() as child_input:
                child_input.write(invocation.stdin)
                child_input.seek(0)
                try:
                    process = subprocess.Popen(
                        invocation.argv,
                        cwd=cwd,
                        stdin=child_input,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                except OSError as error:
                    return _spawn_failure(started, error, invocation.argv[0])
                signal_state.process_group = process.pid
                try:
                    if progress is not None:
                        progress(_duration(started), "starting")
                except OSError as error:
                    _force_cleanup(process)
                    _finish_consumer(consumer_state)
                    return ProcessResult(
                        consumer_state.capture(b""),
                        b"",
                        process.returncode,
                        _duration(started),
                        _presentation_error(error),
                    )
                context = _RunContext(started, timeout, signal_state, output_limits, progress)
                return _collect(process, context, consumer_state)
        except OSError as error:
            if process is not None:
                _force_cleanup(process)
            consumer_error: ResultError | None = None
            if process is not None:
                consumer_error = _finish_consumer(consumer_state)[1]
            failure = ResultError("process_io_error", f"Process I/O failed: {error}.")
            failure = consumer_error or failure
            if signal_state.received is not None:
                failure = ResultError(
                    "interrupted", f"Interrupted by signal {signal_state.received}."
                )
            return ProcessResult(
                consumer_state.capture(b""),
                b"",
                process.returncode if process is not None else None,
                _duration(started),
                failure,
                interrupted_by=signal_state.received,
            )
    finally:
        restore(previous)


def _collect(
    process: subprocess.Popen[bytes],
    context: _RunContext,
    consumer_state: "_ConsumerState",
) -> ProcessResult:
    deadline = context.started + context.timeout
    output = _OutputState({"stdout": bytearray(), "stderr": bytearray()}, consumer_state.consumer)
    selector = selectors.DefaultSelector()
    stdout = cast(BinaryIO, process.stdout)
    stderr = cast(BinaryIO, process.stderr)
    for name, stream in (("stdout", stdout), ("stderr", stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    error: ResultError | None
    timed_out: bool
    cleanup_error: str | None = None
    try:
        error, timed_out = _drain(
            process,
            selector,
            output,
            deadline,
            context,
        )
        needs_cleanup = context.signal_state.received is not None or timed_out or error is not None
        if error is not None:
            output.consumer_failed = True
        if needs_cleanup:
            cleanup_error = _terminate_after_failure(process, selector, output, context)
        finish_error = _finish_consumer(consumer_state)[1]
        if error is None and finish_error is not None:
            error = finish_error
            if not needs_cleanup:
                output.consumer_failed = True
                cleanup_error = _terminate_after_failure(process, selector, output, context)
        elif not needs_cleanup:
            process.wait()
    finally:
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            cast(BinaryIO, key.fileobj).close()
        selector.close()
        if process.poll() is None:
            with suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=FINAL_DRAIN_GRACE)
            except subprocess.TimeoutExpired:
                cleanup_error = cleanup_error or "agent process could not be reaped"
    if context.signal_state.received is not None:
        interrupted = context.signal_state.received
        error = ResultError("interrupted", f"Interrupted by signal {interrupted}.")
    elif timed_out:
        error = ResultError("timeout", f"Agent exceeded the {context.timeout:g} second timeout.")
    if cleanup_error is not None and error is not None:
        error = ResultError(
            error.code, f"{error.message} Process-group cleanup failed: {cleanup_error}."
        )
    return ProcessResult(
        consumer_state.capture(bytes(output.buffers["stdout"])),
        bytes(output.buffers["stderr"]),
        process.returncode,
        _duration(context.started),
        error,
        timed_out,
        context.signal_state.received,
        process.pid,
    )


@dataclass
class _OutputState:
    buffers: dict[str, bytearray]
    consumer: ByteConsumer | None
    consumer_failed: bool = False
    activity: Activity | None = None


@dataclass
class _ConsumerState:
    consumer: ByteConsumer | None
    finished: bool = False
    decoded: DecodedOutput | None = None
    error: ResultError | None = None

    def capture(self, stdout: bytes) -> Capture:
        if self.consumer is None:
            return RawCapture(stdout)
        return ConsumedCapture(self.decoded if self.decoded is not None else DecodedOutput())


def _drain(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    output: _OutputState,
    deadline: float,
    context: _RunContext,
) -> tuple[ResultError | None, bool]:
    last_progress = time.monotonic()
    pending_activity: Activity | None = None
    while True:
        now = time.monotonic()
        native_exit = process.poll()
        if native_exit is not None and not selector.get_map():
            return None, False
        if context.signal_state.received is not None:
            return None, False
        if now >= deadline:
            return None, True
        wait = min(0.05, deadline - now)
        for key, _ in selector.select(max(0.0, wait)):
            read_error = _read_ready(selector, key, output, context.output_limits)
            if read_error is not None:
                return read_error, False
            pending_activity = output.activity or pending_activity
            output.activity = None
        if context.progress is not None:
            elapsed = now - last_progress
            category: Activity | None = pending_activity if elapsed >= PROGRESS_INTERVAL else None
            if category is None and elapsed >= HEARTBEAT_INTERVAL:
                category = "working"
            if category is not None:
                try:
                    context.progress(_duration(context.started), category)
                except OSError as error:
                    return _presentation_error(error), False
                last_progress = now
                pending_activity = None


def _read_ready(
    selector: selectors.BaseSelector,
    key: selectors.SelectorKey,
    output: _OutputState,
    output_limits: OutputLimits,
) -> ResultError | None:
    name = key.data
    try:
        chunk = os.read(key.fd, READ_SIZE)
    except BlockingIOError:
        return None
    except OSError as error:
        return ResultError("output_io_error", f"Cannot read agent {name}: {error}.")
    if not chunk:
        selector.unregister(key.fileobj)
        cast(BinaryIO, key.fileobj).close()
        return None
    if name == "stdout" and output.consumer is not None:
        if output.consumer_failed:
            return None
        try:
            output.activity = output.consumer.feed(chunk) or output.activity
        except ConsumerFailure as failure:
            output.consumer_failed = True
            return failure.error
        return None
    limit = output_limits.stdout if name == "stdout" else output_limits.stderr
    remaining = limit - len(output.buffers[name])
    output.buffers[name].extend(chunk[:remaining])
    if len(chunk) > remaining:
        code: Code = STDOUT_LIMIT_EXCEEDED if name == "stdout" else STDERR_LIMIT_EXCEEDED
        return ResultError(code, f"Agent {name} exceeded {limit} bytes.")
    return None


def _terminate_and_drain(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    output: _OutputState,
    signal_state: _SignalState,
    output_limits: OutputLimits,
) -> None:
    if not signal_state.repeated:
        _signal_group(process.pid, signal.SIGTERM)
    grace_deadline = time.monotonic() + TERMINATE_GRACE
    while time.monotonic() < grace_deadline and not signal_state.repeated:
        for key, _ in selector.select(0.05):
            _read_ready(selector, key, output, output_limits)
        parent_reaped = process.poll() is not None
        if parent_reaped and not selector.get_map() and not _process_group_exists(process.pid):
            break
    _signal_group(process.pid, signal.SIGKILL)
    kill_deadline = time.monotonic() + FINAL_DRAIN_GRACE
    while time.monotonic() < kill_deadline:
        wait = min(0.05, max(0.0, kill_deadline - time.monotonic()))
        for key, _ in selector.select(wait):
            _read_ready(selector, key, output, output_limits)
        parent_reaped = process.poll() is not None
        if parent_reaped and not selector.get_map() and not _process_group_exists(process.pid):
            return
    for key in list(selector.get_map().values()):
        selector.unregister(key.fileobj)
        cast(BinaryIO, key.fileobj).close()
    if process.poll() is None:
        raise OSError("agent process could not be reaped")
    if _process_group_exists(process.pid):
        raise OSError("could not verify owned process-group cleanup before the deadline")


def _terminate_after_failure(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    output: _OutputState,
    context: _RunContext,
) -> str | None:
    try:
        _terminate_and_drain(
            process,
            selector,
            output,
            context.signal_state,
            context.output_limits,
        )
    except OSError as failure:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        return str(failure)
    return None


def _force_cleanup(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()
    with suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=FINAL_DRAIN_GRACE)


def cleanup_process_group(process_group: int) -> str | None:
    try:
        _signal_group(process_group, signal.SIGTERM)
        deadline = time.monotonic() + TERMINATE_GRACE
        while time.monotonic() < deadline and _process_group_exists(process_group):
            time.sleep(0.05)
        if _process_group_exists(process_group):
            _signal_group(process_group, signal.SIGKILL)
            kill_deadline = time.monotonic() + FINAL_DRAIN_GRACE
            while time.monotonic() < kill_deadline and _process_group_exists(process_group):
                time.sleep(0.01)
            if _process_group_exists(process_group):
                return "could not verify owned process-group cleanup before the deadline"
    except OSError as error:
        return str(error)
    return None


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _interrupt_handler(state: _SignalState) -> Handler:
    def escalate() -> None:
        if state.process_group is not None:
            _signal_group(state.process_group, signal.SIGKILL)

    return handler_for(state, on_repeat=escalate)


def _signal_group(process_group: int, chosen: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process_group, chosen)


def _spawn_failure(started: float, error: OSError, executable: str) -> ProcessResult:
    code: Code
    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        code = EXECUTABLE_NOT_FOUND
        message = f"Executable {executable!r} was not found."
    elif isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.ENOEXEC}:
        code = EXECUTABLE_NOT_EXECUTABLE
        message = f"Executable {executable!r} cannot be executed."
    else:
        code = PROCESS_IO_ERROR
        message = f"Cannot start executable {executable!r}: {error}."
    return _startup_failure(started, code, message)


def _startup_failure(started: float, code: Code, message: str) -> ProcessResult:
    return ProcessResult(RawCapture(), b"", None, _duration(started), ResultError(code, message))


def _duration(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _finish_consumer(
    state: _ConsumerState,
) -> tuple[DecodedOutput | None, ResultError | None]:
    if state.consumer is None:
        return None, None
    if state.finished:
        return state.decoded, state.error
    state.finished = True
    try:
        state.decoded = state.consumer.finish()
    except ConsumerFailure as failure:
        state.decoded = failure.decoded or DecodedOutput()
        state.error = failure.error
    return state.decoded, state.error


def _presentation_error(error: OSError) -> ResultError:
    return ResultError("output_io_error", f"Cannot write progress to stderr: {error}.")
