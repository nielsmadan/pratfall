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

from pratfall.models import Invocation, ResultError

STDOUT_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 2 * 1024 * 1024
READ_SIZE = 64 * 1024
TERMINATE_GRACE = 2.0
FINAL_DRAIN_GRACE = 0.25


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    native_exit_code: int | None
    duration_ms: int
    error: ResultError | None = None
    timed_out: bool = False
    interrupted_by: int | None = None


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


def run(
    invocation: Invocation,
    cwd: Path,
    timeout: float,
    *,
    output_limits: OutputLimits = DEFAULT_OUTPUT_LIMITS,
) -> ProcessResult:
    started = time.monotonic()
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
                return _collect(process, started, timeout, signal_state, output_limits)
        except OSError as error:
            if process is not None:
                _force_cleanup(process)
            failure = ResultError("process_io_error", f"Process I/O failed: {error}.")
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
            )
    finally:
        _restore_handlers(previous)


def _collect(
    process: subprocess.Popen[bytes],
    started: float,
    timeout: float,
    signal_state: _SignalState,
    output_limits: OutputLimits,
) -> ProcessResult:
    deadline = started + timeout
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
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
        error, timed_out = _drain(process, selector, buffers, deadline, signal_state, output_limits)
        if signal_state.received is not None or timed_out or error is not None:
            try:
                _terminate_and_drain(process, selector, buffers, signal_state, output_limits)
            except OSError as failure:
                cleanup_error = str(failure)
                with suppress(OSError):
                    os.killpg(process.pid, signal.SIGKILL)
        else:
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
    if signal_state.received is not None:
        interrupted = signal_state.received
        error = ResultError("interrupted", f"Interrupted by signal {interrupted}.")
    elif timed_out:
        error = ResultError("timeout", f"Agent exceeded the {timeout:g} second timeout.")
    if cleanup_error is not None and error is not None:
        error = ResultError(
            error.code, f"{error.message} Process-group cleanup failed: {cleanup_error}."
        )
    return ProcessResult(
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        process.returncode,
        _duration(started),
        error,
        timed_out,
        signal_state.received,
    )


def _drain(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    buffers: dict[str, bytearray],
    deadline: float,
    signal_state: _SignalState,
    output_limits: OutputLimits,
) -> tuple[ResultError | None, bool]:
    while True:
        now = time.monotonic()
        native_exit = process.poll()
        if native_exit is not None and not selector.get_map():
            return None, False
        if signal_state.received is not None:
            return None, False
        if now >= deadline:
            return None, True
        wait = min(0.05, deadline - now)
        for key, _ in selector.select(max(0.0, wait)):
            read_error = _read_ready(selector, key, buffers, output_limits)
            if read_error is not None:
                return read_error, False


def _read_ready(
    selector: selectors.BaseSelector,
    key: selectors.SelectorKey,
    buffers: dict[str, bytearray],
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
    limit = output_limits.stdout if name == "stdout" else output_limits.stderr
    remaining = limit - len(buffers[name])
    buffers[name].extend(chunk[:remaining])
    if len(chunk) > remaining:
        return ResultError(f"{name}_limit_exceeded", f"Agent {name} exceeded {limit} bytes.")
    return None


def _terminate_and_drain(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    buffers: dict[str, bytearray],
    signal_state: _SignalState,
    output_limits: OutputLimits,
) -> None:
    if not signal_state.repeated:
        _signal_group(process.pid, signal.SIGTERM)
    grace_deadline = time.monotonic() + TERMINATE_GRACE
    while time.monotonic() < grace_deadline and not signal_state.repeated:
        for key, _ in selector.select(0.05):
            _read_ready(selector, key, buffers, output_limits)
        process.poll()
    _signal_group(process.pid, signal.SIGKILL)
    kill_deadline = time.monotonic() + FINAL_DRAIN_GRACE
    while time.monotonic() < kill_deadline and selector.get_map():
        for key, _ in selector.select(0.05):
            _read_ready(selector, key, buffers, output_limits)
    for key in list(selector.get_map().values()):
        selector.unregister(key.fileobj)
        cast(BinaryIO, key.fileobj).close()
    try:
        process.wait(timeout=FINAL_DRAIN_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(process.pid, signal.SIGKILL)
        process.wait()


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
