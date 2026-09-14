import errno
import fcntl
import json
import os
import shlex
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol

from pratfall.errors import PratError
from pratfall.models import Activity, Config, Invocation, ResolvedProfile, ResultError
from pratfall.runner import ProcessResult

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


class _Diagnostics(Protocol):
    start_errors: ClassVar[tuple[type[Exception], ...]]
    write_errors: ClassVar[tuple[type[Exception], ...]]

    def open(self) -> None: ...

    def close(self) -> None: ...

    def progress_callback(self) -> Callable[[int, Activity], None] | None: ...

    def fail(self) -> None: ...

    def line(self, value: str) -> None: ...

    def text(self, value: str) -> None: ...

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
        print(value, file=sys.stderr)

    def text(self, value: str) -> None:
        if self.failed:
            return
        sys.stderr.write(value)

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
        self.flags = 0

    def open(self) -> None:
        if self.fd is not None:
            return
        descriptor = sys.stderr.fileno()
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.fd = descriptor
        self.flags = flags

    def close(self) -> None:
        descriptor, self.fd = self.fd, None
        if descriptor is None:
            return
        with suppress(OSError):
            fcntl.fcntl(descriptor, fcntl.F_SETFL, self.flags)

    def progress_callback(self) -> Callable[[int, Activity], None] | None:
        return self.progress

    def fail(self) -> None:
        self.failed = True

    def line(self, value: str) -> None:
        self.text(value + "\n")

    def text(self, value: str) -> None:
        descriptor = self.fd
        if self.failed or descriptor is None:
            return
        data = value.encode("utf-8", errors="replace")
        offset = 0
        while offset < len(data):
            try:
                written = os.write(descriptor, data[offset : offset + 4096])
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return
                self.failed = True
                raise
            if written == 0:
                self.failed = True
                raise OSError(errno.EIO, "stderr write returned zero bytes")
            offset += written

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
        raise PratError(
            f"Cannot write config warnings: {error}.", code="output_io_error"
        ) from error


def _presentation_error(error: Exception) -> ResultError:
    return ResultError("output_io_error", f"Cannot write diagnostics to stderr: {error}.")


@dataclass
class _RunState:
    json_mode: bool = False
    emitted: bool = False
    resolved: ResolvedProfile | None = None
    process: ProcessResult | None = None


def _preview(
    resolved: ResolvedProfile,
    invocation: Invocation,
    cwd: Path,
    state: _RunState,
    *,
    json_mode: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "dry_run": True,
        "agent": resolved.agent.name,
        "profile": resolved.profile,
        "model": resolved.options.model,
        "fast": resolved.options.fast,
        "argv": list(invocation.argv),
        "cwd": str(cwd),
        "timeout": resolved.options.timeout,
        "stdin_bytes": len(invocation.stdin),
    }
    if json_mode:
        _emit_stdout(json.dumps(payload, ensure_ascii=False), state)
        return
    fast = "native" if resolved.options.fast is None else str(resolved.options.fast).lower()
    _emit_stdout(
        "\n".join(
            (
                f"command: {shlex.join(invocation.argv)}",
                f"cwd: {cwd}",
                f"timeout: {resolved.options.timeout:g}s",
                f"fast: {fast}",
                f"stdin: {len(invocation.stdin)} bytes",
            )
        ),
        state,
    )


def _emit_stdout(text: str, state: _RunState) -> None:
    committed = True
    try:
        print(text)
    except UnicodeEncodeError:
        committed = False
        raise
    finally:
        state.emitted = state.emitted or committed


def _emit_result(result: dict[str, object], state: _RunState, *, json_mode: bool) -> None:
    if json_mode:
        _emit_stdout(json.dumps(result, ensure_ascii=False), state)
        return
    output = result["output"]
    if isinstance(output, str) and output:
        _emit_stdout(output.removesuffix("\n"), state)


def _silence_broken_stream(name: Literal["stdout", "stderr"]) -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    target: int | None = None
    try:
        try:
            target = getattr(sys, name).fileno()
            os.dup2(descriptor, target)
        except (AttributeError, OSError, ValueError):
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
        with suppress(OSError, ValueError):
            getattr(sys, name).flush()
    finally:
        if descriptor != target:
            os.close(descriptor)
