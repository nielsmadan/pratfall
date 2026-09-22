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
from typing import ClassVar, Literal, Protocol, TextIO

from pratfall.errors import PratError
from pratfall.models import Activity, Config, Invocation, ResolvedProfile, ResultError

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
        print(value, file=sys.stderr)

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
        self.bytes(value.encode("utf-8", errors="replace"))

    def bytes(self, value: bytes) -> None:
        descriptor = self.fd
        if self.failed or descriptor is None:
            return
        offset = 0
        while offset < len(value):
            try:
                written = os.write(descriptor, value[offset : offset + 4096])
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
class _StdoutLatch:
    emitted: bool = False


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
    if schema is not None:
        payload["schema"] = resolved.options.schema
        payload["schema_transport"] = "temporary prepared file" if schema.path else "inline JSON"
    if json_mode:
        _emit_stdout(json.dumps(payload, ensure_ascii=False), latch)
        return
    fast = "native" if resolved.options.fast is None else str(resolved.options.fast).lower()
    _emit_stdout(
        "\n".join(
            (
                f"command: {shlex.join(argv)}",
                *(
                    [f"schema: {resolved.options.schema} ({payload['schema_transport']})"]
                    if schema is not None
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
