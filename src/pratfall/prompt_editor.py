import os
import shlex
import signal
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path

from pratfall.errors import PratError
from pratfall.interruption import InterruptionState, handler_for, handling
from pratfall.limits import FINAL_DRAIN_GRACE, TERMINATE_GRACE
from pratfall.prompt_input import InputInterrupted, read_edited_prompt

_EDITOR_LAUNCH = """
import os, signal, sys
errors = int(sys.argv[1])
os.set_inheritable(errors, False)
try:
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(0, os.getpgrp())
    signal.signal(signal.SIGTTOU, previous)
    os.execvp(sys.argv[2], sys.argv[2:])
except OSError as error:
    os.write(errors, str(error).encode('utf-8', errors='replace')[:4096])
    sys.exit(127 if isinstance(error, FileNotFoundError) else 126)
"""


def edit_prompt(draft: bytes, invocation_cwd: Path, environ: Mapping[str, str]) -> bytes:
    state = InterruptionState()
    with handling(handler_for(state)):
        try:
            command = _editor_command(environ)
            with tempfile.TemporaryDirectory(prefix="prat-edit-") as directory:
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".md", delete=False) as file:
                    file.write(draft)
                    path = Path(file.name)
                _edit([*command, str(path)], invocation_cwd, state)
                return read_edited_prompt(path, state)
        except (OSError, ValueError, termios.error) as error:
            raise PratError(f"Cannot edit prompt: {error}.", code="invalid_arguments") from error
        finally:
            if state.received is not None:
                raise InputInterrupted(state.received)


def _editor_command(environ: Mapping[str, str]) -> list[str]:
    value = next(
        (value for name in ("VISUAL", "EDITOR") if (value := environ.get(name, "")).strip()),
        "vi",
    )
    command = shlex.split(value)
    if not command or not command[0] or any("\0" in part for part in command):
        raise PratError("Editor command must contain a valid executable.", code="invalid_arguments")
    return command


@contextmanager
def _terminal_ownership_change() -> Iterator[None]:
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGTTOU, previous)


def _edit(command: list[str], invocation_cwd: Path, state: InterruptionState) -> None:
    descriptor = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:
        foreground = os.tcgetpgrp(descriptor)
        attributes = termios.tcgetattr(descriptor)
        try:
            _run_editor(command, invocation_cwd, descriptor, state)
        finally:
            with _terminal_ownership_change():
                try:
                    os.tcsetpgrp(descriptor, foreground)
                finally:
                    termios.tcsetattr(descriptor, termios.TCSANOW, attributes)
    finally:
        os.close(descriptor)


def _run_editor(
    command: list[str], invocation_cwd: Path, descriptor: int, state: InterruptionState
) -> None:
    if state.received is not None:
        return
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as errors, os.fdopen(write_fd, "wb") as report:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _EDITOR_LAUNCH, str(write_fd), *command],
            cwd=invocation_cwd,
            stdin=descriptor,
            stdout=descriptor,
            stderr=descriptor,
            process_group=0,
            pass_fds=(write_fd,),
        )
        report.close()
        try:
            code = _wait_editor(process, state)
            if state.received is None:
                detail = errors.read(4096).decode("utf-8", errors="replace")
                if detail:
                    raise PratError(f"Cannot edit prompt: {detail}.", code="invalid_arguments")
            if code in (-signal.SIGINT, -signal.SIGTERM):
                state.received = state.received or -code
            elif code != 0 and state.received is None:
                raise PratError(f"Editor exited with status {code}.", code="invalid_arguments")
        finally:
            _cleanup(process, state)


def _wait_editor(process: subprocess.Popen[bytes], state: InterruptionState) -> int:
    while state.received is None:
        if process.returncode is not None:
            return process.returncode
        pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
        if pid:
            if os.WIFSTOPPED(status):
                if os.WSTOPSIG(status) in (signal.SIGTTIN, signal.SIGTTOU):
                    _signal_group(process.pid, signal.SIGCONT)
                else:
                    raise PratError(
                        f"Editor stopped by signal {os.WSTOPSIG(status)}.", code="invalid_arguments"
                    )
            else:
                process.returncode = os.waitstatus_to_exitcode(status)
                return process.returncode
        time.sleep(0.02)
    return 0


def _signal_group(group: int, chosen: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(group, chosen)


def _group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cleanup(process: subprocess.Popen[bytes], state: InterruptionState) -> None:
    start = time.monotonic()
    _signal_group(process.pid, signal.SIGTERM)
    _signal_group(process.pid, signal.SIGCONT)
    killed = False
    while True:
        reaped = process.poll() is not None
        if not _group_exists(process.pid) and reaped:
            return
        elapsed = time.monotonic() - start
        if not killed and (state.repeated or elapsed >= TERMINATE_GRACE):
            _signal_group(process.pid, signal.SIGKILL)
            killed = True
        if elapsed >= TERMINATE_GRACE + FINAL_DRAIN_GRACE:
            raise PratError("Could not clean up editor process group.", code="invalid_arguments")
        time.sleep(0.02)
