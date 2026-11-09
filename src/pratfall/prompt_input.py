import errno
import json
import os
import select
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, TextIO, cast

from pratfall.errors import PratError
from pratfall.interruption import InterruptionState, handler_for, handling

_MIB = 1024 * 1024
STDIN_PROMPT_LIMIT = 8 * _MIB
ARGUMENT_PROMPT_LIMIT = _MIB
TEMPLATE_LIMIT = _MIB
_READ_SIZE = 64 * 1024
_CONTEXT_PREFIX = b"# Context: "
_CONTEXT_OVERHEAD = len(_CONTEXT_PREFIX) + 4
_STDIN_LABEL = b"stdin"
_PROMPT_ADVICE = "; provide exactly one prompt another way"
STDIN_CONTEXT = "-"


@dataclass(frozen=True)
class PromptSource:
    kind: Literal["inline", "file", "stdin"]
    value: str | None = None
    flag: str | None = None


@dataclass(frozen=True)
class PromptLimit:
    size: int
    agent: str | None = None


MAX_PROMPT = PromptLimit(STDIN_PROMPT_LIMIT)


def delivery_limit(delivery: Literal["stdin", "argument"]) -> int:
    return STDIN_PROMPT_LIMIT if delivery == "stdin" else ARGUMENT_PROMPT_LIMIT


def mib_text(size: int) -> str:
    return f"{size // _MIB} MiB"


def prompt_too_large(size: int | None, limit: PromptLimit) -> PratError:
    amount = f"more than {limit.size}" if size is None else str(size)
    owner = "" if limit.agent is None else f" for {limit.agent}"
    return PratError(
        f"prompt is {amount} bytes; the limit{owner} is {limit.size} ({mib_text(limit.size)})",
        code="invalid_arguments",
    )


class InputInterrupted(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


def acquire_prompt(
    source: PromptSource | None,
    invocation_cwd: Path,
    stdin: BinaryIO | TextIO | None,
    *,
    allow_absent: bool = False,
    allow_blank: bool = False,
    limit: PromptLimit = MAX_PROMPT,
) -> bytes:
    implicit = source is None
    if source is None:
        if allow_absent and (stdin is None or stdin.closed):
            return b""
        if _stdin_is_terminal(stdin):
            if allow_absent:
                return b""
            raise PratError(
                "provide exactly one prompt as text, --file PATH, or redirected standard input",
                code="invalid_arguments",
            )
        source = PromptSource("stdin")
    state = InterruptionState()
    with _input_signal_handlers(state):
        if source.kind == "inline":
            value = _encode_inline(cast(str, source.value))
            _check_size(len(value), limit)
        elif source.kind == "file" and source.value != "-":
            value = _read_file(
                cast(str, source.value),
                invocation_cwd,
                state,
                limit=limit.size,
                label="prompt file",
                sized=_size_check(0, limit),
            )
            _check_read(value, 0, limit)
        else:
            value = _check_read(_read_stdin(stdin, state, limit=limit.size), 0, limit)
            if implicit and allow_absent and not value:
                return b""
            return _validate(value, "standard input", allow_empty=allow_blank)
        _raise_if_interrupted(state)
        return _validate(value, _source_label(source), allow_empty=allow_blank)


def _source_label(source: PromptSource) -> str:
    if source.kind == "file":
        return f"prompt file {source.value}"
    return source.flag or "prompt"


def _stdin_is_terminal(stdin: BinaryIO | TextIO | None) -> bool:
    try:
        return cast(BinaryIO | TextIO, stdin).isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _encode_inline(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PratError("prompt is not valid UTF-8", code="invalid_arguments") from error


def prepend_contexts(
    paths: tuple[str, ...],
    prompt: bytes,
    invocation_cwd: Path,
    stdin: BinaryIO | TextIO | None = None,
    *,
    limit: PromptLimit = MAX_PROMPT,
) -> bytes:
    if not paths:
        return prompt
    state = InterruptionState()
    with _input_signal_handlers(state):
        consumed = len(prompt)
        headers: list[bytes] = []
        for path in paths:
            _raise_if_interrupted(state)
            label_size = len(_STDIN_LABEL) if path == STDIN_CONTEXT else 2
            for character in "" if path == STDIN_CONTEXT else path:
                label_size += len(json.dumps(character)) - 2
                _check_size(consumed + label_size + _CONTEXT_OVERHEAD, limit)
            consumed += label_size + _CONTEXT_OVERHEAD
            _check_size(consumed, limit)
            headers.append(_CONTEXT_PREFIX + _context_label(path) + b"\n\n")
        parts: list[bytes] = []
        for path, header in zip(paths, headers, strict=True):
            _raise_if_interrupted(state)
            budget = limit.size - consumed
            if path == STDIN_CONTEXT:
                content = _read_stdin(stdin, state, limit=budget, advice="")
                label = "standard input context"
            else:
                content = _read_file(
                    path,
                    invocation_cwd,
                    state,
                    limit=budget,
                    label="context file",
                    sized=_size_check(consumed, limit),
                )
                label = "context file"
            _validate(_check_read(content, consumed, limit), label, allow_empty=True)
            consumed += len(content)
            parts.extend((header, content, b"\n\n"))
        parts.append(prompt)
        return b"".join(parts)


def _context_label(path: str) -> bytes:
    return _STDIN_LABEL if path == STDIN_CONTEXT else json.dumps(path).encode("ascii")


def _check_size(size: int, limit: PromptLimit) -> None:
    if size > limit.size:
        raise prompt_too_large(size, limit)


def _size_check(consumed: int, limit: PromptLimit) -> Callable[[int], None]:
    return lambda size: _check_size(consumed + size, limit)


def _check_read(content: bytes, consumed: int, limit: PromptLimit) -> bytes:
    if consumed + len(content) > limit.size:
        raise prompt_too_large(None, limit)
    return content


def _read_file(
    value: str,
    invocation_cwd: Path,
    state: InterruptionState,
    *,
    limit: int,
    label: str,
    sized: Callable[[int], None] = lambda _size: None,
) -> bytes:
    path = invocation_cwd / value
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PratError(
            f"cannot open {label} {path}: {error.strerror or error}",
            code="invalid_arguments",
        ) from error
    try:
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            raise PratError(
                f"cannot inspect {label} {path}: {error.strerror or error}",
                code="invalid_arguments",
            ) from error
        if not stat.S_ISREG(info.st_mode):
            raise PratError(f"{label} is not a regular file: {path}", code="invalid_arguments")
        sized(info.st_size)
        return _read_descriptor(descriptor, state, wait=False, label=f"{label} {path}", limit=limit)
    finally:
        os.close(descriptor)


def _read_stdin(
    stdin: BinaryIO | TextIO | None,
    state: InterruptionState,
    *,
    limit: int,
    advice: str = _PROMPT_ADVICE,
) -> bytes:
    stream = cast(BinaryIO | TextIO, getattr(stdin, "buffer", stdin))
    if stream is None:
        raise PratError(f"cannot read standard input{advice}", code="invalid_arguments")
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return _read_stream(stream, state, limit=limit, advice=advice)
    return _read_descriptor(descriptor, state, wait=True, label="standard input", limit=limit)


def _read_stream(
    stream: BinaryIO | TextIO,
    state: InterruptionState,
    *,
    limit: int,
    advice: str = _PROMPT_ADVICE,
) -> bytes:
    _raise_if_interrupted(state)
    try:
        value = stream.read(limit + 1)
    except (OSError, ValueError) as error:
        raise PratError(
            f"cannot read standard input{advice}: {error}", code="invalid_arguments"
        ) from error
    _raise_if_interrupted(state)
    if isinstance(value, str):
        return _encode_inline(value)
    if not isinstance(value, bytes):
        raise PratError("cannot read standard input as bytes", code="invalid_arguments")
    return value


def _read_descriptor(
    descriptor: int,
    state: InterruptionState,
    *,
    wait: bool,
    label: str,
    limit: int,
) -> bytes:
    value = bytearray()
    while len(value) <= limit:
        _raise_if_interrupted(state)
        if wait:
            try:
                readable, _, _ = select.select([descriptor], [], [], 0.05)
            except (OSError, ValueError) as error:
                raise PratError(
                    f"cannot read {label}: {error}", code="invalid_arguments"
                ) from error
            if not readable:
                continue
        try:
            chunk = os.read(descriptor, min(_READ_SIZE, limit + 1 - len(value)))
        except BlockingIOError:
            continue
        except OSError as error:
            if error.errno == errno.EINTR:
                continue
            raise PratError(
                f"cannot read {label}: {error.strerror or error}", code="invalid_arguments"
            ) from error
        if not chunk:
            break
        value.extend(chunk)
    return bytes(value)


def _validate(value: bytes, label: str, *, allow_empty: bool = False) -> bytes:
    if b"\0" in value:
        raise PratError("prompt must not contain NUL bytes", code="invalid_arguments")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PratError(f"{label} is not valid UTF-8", code="invalid_arguments") from error
    if not allow_empty and not text.strip():
        raise PratError(f"{label} is empty or whitespace-only", code="invalid_arguments")
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


def read_edited_prompt(path: Path, state: InterruptionState, limit: PromptLimit) -> bytes:
    _raise_if_interrupted(state)
    value = _read_file(
        str(path),
        path.parent,
        state,
        limit=limit.size,
        label="edited prompt",
        sized=_size_check(0, limit),
    )
    _raise_if_interrupted(state)
    return _validate(_check_read(value, 0, limit), "edited prompt")


def read_bounded_file(path: Path, *, limit: int, label: str) -> bytes:
    state = InterruptionState()
    with _input_signal_handlers(state):
        return _read_file(str(path), path.parent, state, limit=limit, label=label)
