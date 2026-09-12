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
from types import FrameType
from typing import BinaryIO, cast

from pratfall.consumer import ByteConsumer, ConsumerFailure
from pratfall.models import DecodedOutput, Invocation, ResultError

STDOUT_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 2 * 1024 * 1024
READ_SIZE = 64 * 1024
TERMINATE_GRACE = 2.0
FINAL_DRAIN_GRACE = 0.25
PROGRESS_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 5.0


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    native_exit_code: int | None
    duration_ms: int
    error: ResultError | None = None
    timed_out: bool = False
    interrupted_by: int | None = None
    decoded: DecodedOutput | None = None
    process_group: int | None = None


@dataclass(frozen=True)
class OutputLimits:
    stdout: int = STDOUT_LIMIT
    stderr: int = STDERR_LIMIT


DEFAULT_OUTPUT_LIMITS = OutputLimits()


@dataclass
class _SignalState:
    received: int | None = None
    repeated: bool = False
    process_group: int | None = None


@dataclass(frozen=True)
class _RunContext:
    started: float
    timeout: float
    signal_state: _SignalState
    output_limits: OutputLimits
    progress: Callable[[int, str], None] | None


def run(
    invocation: Invocation,
    cwd: Path,
    timeout: float,
    *,
    output_limits: OutputLimits = DEFAULT_OUTPUT_LIMITS,
    consumer: ByteConsumer | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> ProcessResult:
    started = time.monotonic()
    consumer_state = _ConsumerState(consumer)
    if os.name != "posix":
        return _startup_failure(
            started, "unsupported_platform", "Process execution requires POSIX."
        )
    signal_state = _SignalState()
    previous = _install_handlers(signal_state)
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
                    decoded_output = _finish_consumer(consumer_state)[0]
                    return ProcessResult(
                        b"",
                        b"",
                        process.returncode,
                        _duration(started),
                        _presentation_error(error),
                        decoded=decoded_output,
                    )
                context = _RunContext(started, timeout, signal_state, output_limits, progress)
                return _collect(process, context, consumer_state)
        except OSError as error:
            if process is not None:
                _force_cleanup(process)
            decoded_output = None
            consumer_error: ResultError | None = None
            if process is not None:
                decoded_output, consumer_error = _finish_consumer(consumer_state)
            failure = ResultError("process_io_error", f"Process I/O failed: {error}.")
            failure = consumer_error or failure
            if signal_state.received is not None:
                failure = ResultError(
                    "interrupted", f"Interrupted by signal {signal_state.received}."
                )
            return ProcessResult(
                b"",
                b"",
                process.returncode if process is not None else None,
                _duration(started),
                failure,
                interrupted_by=signal_state.received,
                decoded=decoded_output,
            )
    finally:
        _restore_handlers(previous)


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
    decoded: DecodedOutput | None = None
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
            try:
                _terminate_and_drain(
                    process, selector, output, context.signal_state, context.output_limits
                )
            except OSError as failure:
                cleanup_error = str(failure)
                with suppress(OSError):
                    os.killpg(process.pid, signal.SIGKILL)
        decoded, finish_error = _finish_consumer(consumer_state)
        if error is None and finish_error is not None:
            error = finish_error
            if not needs_cleanup:
                output.consumer_failed = True
                try:
                    _terminate_and_drain(
                        process, selector, output, context.signal_state, context.output_limits
                    )
                except OSError as failure:
                    cleanup_error = str(failure)
                    with suppress(OSError):
                        os.killpg(process.pid, signal.SIGKILL)
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
        b"" if consumer_state.consumer is not None else bytes(output.buffers["stdout"]),
        bytes(output.buffers["stderr"]),
        process.returncode,
        _duration(context.started),
        error,
        timed_out,
        context.signal_state.received,
        decoded,
        process.pid,
    )


@dataclass
class _OutputState:
    buffers: dict[str, bytearray]
    consumer: ByteConsumer | None
    consumer_failed: bool = False
    activity: str | None = None


@dataclass
class _ConsumerState:
    consumer: ByteConsumer | None
    finished: bool = False
    decoded: DecodedOutput | None = None
    error: ResultError | None = None


def _drain(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    output: _OutputState,
    deadline: float,
    context: _RunContext,
) -> tuple[ResultError | None, bool]:
    last_progress = time.monotonic()
    pending_activity: str | None = None
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
            category = pending_activity if elapsed >= PROGRESS_INTERVAL else None
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
        return ResultError(f"{name}_limit_exceeded", f"Agent {name} exceeded {limit} bytes.")
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


def _install_handlers(
    state: _SignalState,
) -> dict[signal.Signals, Callable[[int, FrameType | None], None] | int | None]:
    previous: dict[signal.Signals, Callable[[int, FrameType | None], None] | int | None] = {}

    def receive(signum: int, _frame: FrameType | None) -> None:
        if state.received is None:
            state.received = signum
        else:
            state.repeated = True
            if state.process_group is not None:
                _signal_group(state.process_group, signal.SIGKILL)

    for chosen in (signal.SIGINT, signal.SIGTERM):
        previous[chosen] = signal.getsignal(chosen)
        signal.signal(chosen, receive)
    return previous


def _restore_handlers(
    previous: dict[signal.Signals, Callable[[int, FrameType | None], None] | int | None],
) -> None:
    for chosen, handler in previous.items():
        signal.signal(chosen, handler)


def _signal_group(process_group: int, chosen: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process_group, chosen)


def _spawn_failure(started: float, error: OSError, executable: str) -> ProcessResult:
    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        code = "executable_not_found"
        message = f"Executable {executable!r} was not found."
    elif isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.ENOEXEC}:
        code = "executable_not_executable"
        message = f"Executable {executable!r} cannot be executed."
    else:
        code = "process_io_error"
        message = f"Cannot start executable {executable!r}: {error}."
    return _startup_failure(started, code, message)


def _startup_failure(started: float, code: str, message: str) -> ProcessResult:
    return ProcessResult(b"", b"", None, _duration(started), ResultError(code, message))


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
