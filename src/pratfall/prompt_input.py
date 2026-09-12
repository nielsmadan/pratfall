import errno
import os
import select
import signal
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Literal, TextIO, cast

from pratfall.errors import PratError

PROMPT_LIMIT = 1024 * 1024
_READ_SIZE = 64 * 1024


@dataclass(frozen=True)
class PromptSource:
    kind: Literal["inline", "file", "stdin"]
    value: str | None = None


class InputInterrupted(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(f"Interrupted by signal {signum}.")
        self.signum = signum


@dataclass
class _SignalState:
    received: int | None = None


def acquire_prompt(source: PromptSource | None, invocation_cwd: Path) -> bytes:
    if source is None:
        if _stdin_is_terminal():
            raise PratError(
                "Provide exactly one prompt as text, --file PATH, or redirected standard input.",
                code="invalid_arguments",
            )
        source = PromptSource("stdin")
    state = _SignalState()
    with _input_signal_handlers(state):
        if source.kind == "inline":
            value = _encode_inline(cast(str, source.value))
            label = "Prompt"
        elif source.kind == "file" and source.value != "-":
            value = _read_file(cast(str, source.value), invocation_cwd, state)
            label = "Prompt file"
        else:
            value = _read_stdin(state)
            label = "Standard input prompt"
        _raise_if_interrupted(state)
        return _validate(value, label)


def _stdin_is_terminal() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _encode_inline(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PratError("Prompt is not valid UTF-8.", code="invalid_arguments") from error


def _read_file(value: str, invocation_cwd: Path, state: _SignalState) -> bytes:
    path = Path(os.path.abspath(invocation_cwd / value))
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PratError(
            f"Cannot open prompt file {path}: {error.strerror or error}.",
            code="invalid_arguments",
        ) from error
    try:
        try:
            mode = os.fstat(descriptor).st_mode
        except OSError as error:
            raise PratError(
                f"Cannot inspect prompt file {path}: {error.strerror or error}.",
                code="invalid_arguments",
            ) from error
        if not stat.S_ISREG(mode):
            raise PratError(f"Prompt file is not a regular file: {path}", code="invalid_arguments")
        return _read_descriptor(descriptor, state, wait=False, label=f"prompt file {path}")
    finally:
        os.close(descriptor)


def _read_stdin(state: _SignalState) -> bytes:
    stream = cast(BinaryIO | TextIO, getattr(sys.stdin, "buffer", sys.stdin))
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return _read_stream(stream, state)
    return _read_descriptor(descriptor, state, wait=True, label="standard input")


def _read_stream(stream: BinaryIO | TextIO, state: _SignalState) -> bytes:
    _raise_if_interrupted(state)
    try:
        value = stream.read(PROMPT_LIMIT + 1)
    except (OSError, ValueError) as error:
        raise PratError(
            f"Cannot read standard input; provide exactly one prompt another way: {error}.",
            code="invalid_arguments",
        ) from error
    _raise_if_interrupted(state)
    if isinstance(value, str):
        return _encode_inline(value)
    if not isinstance(value, bytes):
        raise PratError("Cannot read standard input as bytes.", code="invalid_arguments")
    return value


def _read_descriptor(descriptor: int, state: _SignalState, *, wait: bool, label: str) -> bytes:
    value = bytearray()
    while len(value) <= PROMPT_LIMIT:
        _raise_if_interrupted(state)
        if wait:
            try:
                readable, _, _ = select.select([descriptor], [], [], 0.05)
            except (OSError, ValueError) as error:
                raise PratError(
                    f"Cannot read {label}: {error}.", code="invalid_arguments"
                ) from error
            if not readable:
                continue
        try:
            chunk = os.read(descriptor, min(_READ_SIZE, PROMPT_LIMIT + 1 - len(value)))
        except BlockingIOError:
            continue
        except OSError as error:
            if error.errno == errno.EINTR:
                continue
            raise PratError(
                f"Cannot read {label}: {error.strerror or error}.", code="invalid_arguments"
            ) from error
        if not chunk:
            break
        value.extend(chunk)
    return bytes(value)


def _validate(value: bytes, label: str) -> bytes:
    if len(value) > PROMPT_LIMIT:
        raise PratError(f"Prompt exceeds the {PROMPT_LIMIT} byte limit.", code="invalid_arguments")
    if b"\0" in value:
        raise PratError("Prompt must not contain NUL bytes.", code="invalid_arguments")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PratError(f"{label} is not valid UTF-8.", code="invalid_arguments") from error
    if not text.strip():
        raise PratError("Prompt must not be empty or whitespace-only.", code="invalid_arguments")
    return value


def _raise_if_interrupted(state: _SignalState) -> None:
    if state.received is not None:
        raise InputInterrupted(state.received)


@contextmanager
def _input_signal_handlers(state: _SignalState) -> Iterator[None]:
    previous: dict[signal.Signals, Callable[[int, FrameType | None], None] | int | None] = {}

    def receive(signum: int, _frame: FrameType | None) -> None:
        if state.received is None:
            state.received = signum

    try:
        for chosen in (signal.SIGINT, signal.SIGTERM):
            previous[chosen] = signal.getsignal(chosen)
            signal.signal(chosen, receive)
        yield
    finally:
        for chosen, handler in previous.items():
            signal.signal(chosen, handler)
        _raise_if_interrupted(state)
