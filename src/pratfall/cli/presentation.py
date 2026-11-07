import json
import os
import shlex
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol, TextIO

from pratfall.errors import PratError
from pratfall.limits import WORKFLOW_EACH_ITEMS
from pratfall.models import (
    Activity,
    Config,
    Invocation,
    ResolvedProfile,
    ResultError,
    WorkflowStep,
)
from pratfall.nonblocking import write_nonblocking

ACTIVITY_LABELS: Mapping[Activity, str] = MappingProxyType(
    {
        "starting": "starting",
        "working": "working",
        "reasoning": "reasoning",
        "tool": "using tools",
        "answering": "answering",
        "finishing": "finishing",
    }
)


def _emit(payload: dict[str, object], lines: list[str], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"schema_version": 1, **payload}, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


# devnull replacements assigned to sys.stdout/stderr stay referenced for the
# process lifetime, so a broken-stream recovery is not garbage-collected and
# closed under the test harness (pytest hooks ResourceWarning on the GC close).
_DEVNULL_STREAMS: dict[str, TextIO] = {}


def _limit_text(step: WorkflowStep) -> str:
    text = "" if step.max_items == WORKFLOW_EACH_ITEMS else f" max_items={step.max_items}"
    return text + (" overflow=truncate" if step.overflow == "truncate" else "")


_CONTROL_ESCAPES = {
    code: f"\\x{code:02x}" for code in (*range(0x20), *range(0x7F, 0xA0)) if code != ord("\t")
} | {ord("\n"): "\\n", ord("\r"): "\\r"}


def _escape_controls(text: str) -> str:
    return text.translate(_CONTROL_ESCAPES)


_TABLE_ESCAPES = _CONTROL_ESCAPES | {ord("\t"): "\\t"}
_MIN_CUT_WIDTH = 20


def display_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _cut(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        used += _char_width(char)
        if used > width - 1:
            break
        kept.append(char)
    return "".join(kept) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * (width - display_width(text))


def render_table(
    rows: Sequence[Sequence[str]], *, cut: int | None = None, width: int | None = None
) -> list[str]:
    cells = [[cell.translate(_TABLE_ESCAPES) or "-" for cell in row] for row in rows]
    widths = [max(display_width(row[index]) for row in cells) for index in range(len(cells[0]))]
    if cut is not None and width is not None:
        others = sum(widths) - widths[cut] + 2 * (len(widths) - 1)
        widths[cut] = min(widths[cut], max(width - others, _MIN_CUT_WIDTH))
        cells = [
            [_cut(cell, widths[cut]) if index == cut else cell for index, cell in enumerate(row)]
            for row in cells
        ]
    return [
        "  ".join(
            [*(_pad(cell, size) for cell, size in zip(row[:-1], widths, strict=False)), row[-1]]
        )
        for row in cells
    ]


def _print_diagnostic(message: object) -> None:
    print(f"prat: {_escape_controls(str(message))}", file=sys.stderr)


class _Diagnostics(Protocol):
    start_errors: ClassVar[tuple[type[Exception], ...]]
    write_errors: ClassVar[tuple[type[Exception], ...]]

    def open(self) -> None: ...

    def close(self) -> None: ...

    def progress_callback(self) -> Callable[[int, Activity], None] | None: ...

    def fail(self) -> None: ...

    def line(self, value: str) -> None: ...

    def text(self, value: str) -> None: ...

    def bytes(self, value: bytes) -> None: ...

    def flush(self) -> None: ...


class _StreamDiagnostics:
    start_errors: ClassVar[tuple[type[Exception], ...]] = (OSError, ValueError)
    write_errors: ClassVar[tuple[type[Exception], ...]] = (OSError, ValueError)

    def __init__(self) -> None:
        self.failed = False

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def progress_callback(self) -> Callable[[int, Activity], None] | None:
        return None

    def fail(self) -> None:
        self.failed = True
        _silence_broken_stream("stderr")

    def line(self, value: str) -> None:
        if self.failed:
            return
        print(_escape_controls(value), file=sys.stderr)

    def text(self, value: str) -> None:
        if self.failed:
            return
        sys.stderr.write(value)

    def bytes(self, value: bytes) -> None:
        if self.failed:
            return
        stream = getattr(sys.stderr, "buffer", None)
        if stream is None:
            sys.stderr.write(value.decode("utf-8"))
        else:
            stream.write(value)

    def flush(self) -> None:
        if self.failed:
            return
        sys.stderr.flush()


class _ProgressDiagnostics:
    start_errors: ClassVar[tuple[type[Exception], ...]] = (AttributeError, OSError, ValueError)
    write_errors: ClassVar[tuple[type[Exception], ...]] = (OSError,)

    def __init__(self) -> None:
        self.failed = False
        self.fd: int | None = None

    def open(self) -> None:
        if self.fd is None:
            self.fd = sys.stderr.fileno()

    def close(self) -> None:
        self.fd = None

    def progress_callback(self) -> Callable[[int, Activity], None] | None:
        return self.progress

    def fail(self) -> None:
        self.failed = True

    def line(self, value: str) -> None:
        self.text(_escape_controls(value) + "\n")

    def text(self, value: str) -> None:
        self.bytes(value.encode("utf-8", errors="replace"))

    def bytes(self, value: bytes) -> None:
        descriptor = self.fd
        if self.failed or descriptor is None:
            return
        try:
            write_nonblocking(descriptor, value)
        except OSError:
            self.failed = True
            raise

    def flush(self) -> None:
        return None

    def progress(self, elapsed_ms: int, category: Activity) -> None:
        self.line(f"prat: {elapsed_ms / 1000:.1f}s {ACTIVITY_LABELS[category]}")


def _diagnostics_for(*, progress: bool) -> _Diagnostics:
    return _ProgressDiagnostics() if progress else _StreamDiagnostics()


def _config_warnings(config: Config, diagnostics: _Diagnostics) -> None:
    if not config.warnings:
        return
    try:
        diagnostics.open()
        for warning in config.warnings:
            diagnostics.line(f"prat: warning: {warning}")
        diagnostics.flush()
    except (AttributeError, OSError, ValueError) as error:
        diagnostics.close()
        _silence_broken_stream("stderr")
        raise PratError(f"cannot write config warnings: {error}", code="output_io_error") from error


def _guard[T](action: Callable[[], T], recover: Callable[[Exception], T]) -> T:
    try:
        return action()
    except BrokenPipeError:
        raise
    except Exception as error:  # noqa: BLE001 - last resort so no failure escapes as a traceback
        return recover(error)


def _printable(error: Exception) -> str:
    text = _guard(lambda: f"{type(error).__name__}: {error}", lambda _: type(error).__name__)
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _internal_message(error: Exception) -> str:
    return f"unexpected failure: {_printable(error)}"


def _stderr_is_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except (ValueError, OSError):
        return False


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _status_lines(*, progress: bool) -> bool:
    return progress or _stderr_is_tty()


def _presentation_error(error: Exception) -> ResultError:
    return ResultError("output_io_error", f"cannot write diagnostics to stderr: {error}")


@dataclass
class _StdoutLatch:
    emitted: bool = False


_SCHEMA_TRANSPORT_TEXT = {"file": "temporary prepared file", "inline": "inline JSON"}


def _preview(
    resolved: ResolvedProfile,
    invocation: Invocation,
    cwd: Path,
    latch: _StdoutLatch,
    *,
    json_mode: bool,
) -> None:
    argv = list(invocation.argv)
    schema = resolved.prepared_schema
    if schema is not None and schema.path is not None:
        argv = [arg.replace(schema.path, "<temporary prepared schema>") for arg in argv]
    payload = {
        "schema_version": 1,
        "dry_run": True,
        "agent": resolved.agent.name,
        "profile": resolved.profile,
        "model": resolved.options.model,
        "fast": resolved.options.fast,
        "argv": argv,
        "cwd": str(cwd),
        "timeout": resolved.options.timeout,
        "stdin_bytes": len(invocation.stdin),
    }
    if invocation.env:
        payload["env"] = dict(invocation.env)
    transport = None if schema is None else "file" if schema.path else "inline"
    if transport is not None:
        payload["schema"] = resolved.options.schema
        payload["schema_transport"] = transport
    if json_mode:
        _emit_stdout(json.dumps(payload, ensure_ascii=False), latch)
        return
    fast = "native" if resolved.options.fast is None else str(resolved.options.fast).lower()
    command = (
        ["env", *(f"{key}={value}" for key, value in invocation.env), *argv]
        if invocation.env
        else argv
    )
    _emit_stdout(
        "\n".join(
            (
                f"command: {shlex.join(command)}",
                *(
                    [f"schema: {resolved.options.schema} ({_SCHEMA_TRANSPORT_TEXT[transport]})"]
                    if transport is not None
                    else []
                ),
                f"cwd: {cwd}",
                f"timeout: {resolved.options.timeout:g}s",
                f"fast: {fast}",
                f"stdin: {len(invocation.stdin)} bytes",
            )
        ),
        latch,
    )


def _emit_stdout(text: str, latch: _StdoutLatch) -> None:
    committed = True
    try:
        print(text)
    except UnicodeEncodeError:
        committed = False
        raise
    finally:
        latch.emitted = latch.emitted or committed


def _emit_result(result: dict[str, object], latch: _StdoutLatch, *, json_mode: bool) -> None:
    if json_mode:
        _emit_stdout(json.dumps(result, ensure_ascii=False), latch)
        return
    output = result["output"]
    if isinstance(output, str) and output:
        _emit_stdout(output.removesuffix("\n"), latch)


def _silence_broken_stream(name: Literal["stdout", "stderr"]) -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    target: int | None = None
    try:
        try:
            target = getattr(sys, name).fileno()
            os.dup2(descriptor, target)
        except (AttributeError, OSError, ValueError):
            previous = _DEVNULL_STREAMS.get(name)
            if previous is not None:
                with suppress(OSError):
                    previous.close()
            replacement = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
            _DEVNULL_STREAMS[name] = replacement
            setattr(sys, name, replacement)
        with suppress(OSError, ValueError):
            getattr(sys, name).flush()
    finally:
        if descriptor != target:
            os.close(descriptor)
