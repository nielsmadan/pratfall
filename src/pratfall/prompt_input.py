import errno
import os
import select
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, TextIO, cast

from pratfall.errors import PratError
from pratfall.interruption import InterruptionState, handler_for, handling

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


def acquire_prompt(
    source: PromptSource | None, invocation_cwd: Path, *, allow_absent: bool = False
) -> bytes:
    implicit = source is None
    if source is None:
        if allow_absent and (sys.stdin is None or sys.stdin.closed):
            return b""
        if _stdin_is_terminal():
            if allow_absent:
                return b""
            raise PratError(
                "Provide exactly one prompt as text, --file PATH, or redirected standard input.",
                code="invalid_arguments",
            )
        source = PromptSource("stdin")
    state = InterruptionState()
    with _input_signal_handlers(state):
        if source.kind == "inline":
            value = _encode_inline(cast(str, source.value))
            label = "Prompt"
        elif source.kind == "file" and source.value != "-":
            value = _read_file(cast(str, source.value), invocation_cwd, state)
            label = "Prompt file"
        else:
            value = _read_stdin(state)
            if implicit and allow_absent and not value:
                return b""
            return _validate(value, "Standard input prompt")
        _raise_if_interrupted(state)
        value = _validate(value, label)
        if sys.stdin is not None and not sys.stdin.closed and not _stdin_is_terminal():
            stdin = _read_stdin(state)
            if stdin:
                value = _validate(stdin + b"\n\n" + value, "Combined prompt")
        return value


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


def _read_file(value: str, invocation_cwd: Path, state: InterruptionState) -> bytes:
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


def _read_stdin(state: InterruptionState) -> bytes:
    stream = cast(BinaryIO | TextIO, getattr(sys.stdin, "buffer", sys.stdin))
    if stream is None:
        raise PratError(
            "Cannot read standard input; provide exactly one prompt another way.",
            code="invalid_arguments",
        )
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return _read_stream(stream, state)
    return _read_descriptor(descriptor, state, wait=True, label="standard input")


def _read_stream(stream: BinaryIO | TextIO, state: InterruptionState) -> bytes:
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


def _read_descriptor(descriptor: int, state: InterruptionState, *, wait: bool, label: str) -> bytes:
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


def _raise_if_interrupted(state: InterruptionState) -> None:
    if state.received is not None:
        raise InputInterrupted(state.received)


@contextmanager
def _input_signal_handlers(state: InterruptionState) -> Iterator[None]:
    try:
        with handling(handler_for(state)):
            yield
    finally:
        _raise_if_interrupted(state)
