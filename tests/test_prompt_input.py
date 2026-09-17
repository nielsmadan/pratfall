import io
import os
import signal
import sys
from pathlib import Path

import pytest

from pratfall import prompt_input
from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT, InputInterrupted, PromptSource, acquire_prompt


@pytest.mark.parametrize("stdin", [b"", b"3", "café\n雪\n".encode(), b" \n"])
def test_combines_stdin_before_inline_prompt_preserving_bytes(
    stdin: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.BytesIO(stdin))
    expected = stdin + b"\n\ntimes 5" if stdin else b"times 5"
    assert acquire_prompt(PromptSource("inline", "times 5"), tmp_path) == expected


@pytest.mark.parametrize("kind", ["terminal", "closed", "unavailable"])
def test_inline_prompt_works_without_redirected_stdin(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.BytesIO(b"terminal input")
    if kind == "terminal":
        monkeypatch.setattr(stream, "isatty", lambda: True)
    elif kind == "closed":
        stream.close()
    monkeypatch.setattr(sys, "stdin", None if kind == "unavailable" else stream)
    assert acquire_prompt(PromptSource("inline", "prompt"), tmp_path) == b"prompt"
    if kind == "terminal":
        assert stream.tell() == 0


@pytest.mark.parametrize(
    ("stdin", "message"),
    [(b"\xff", "valid UTF-8"), (b"x\0y", "NUL"), (b"x" * PROMPT_LIMIT, "byte limit")],
)
def test_combined_prompt_rejects_invalid_stdin(
    stdin: bytes, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.BytesIO(stdin))
    with pytest.raises(PratError, match=message):
        acquire_prompt(PromptSource("inline", "prompt"), tmp_path)


@pytest.mark.parametrize("excess", [0, 1])
def test_combined_prompt_byte_limit_includes_separator(
    excess: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdin = b"x" * (PROMPT_LIMIT - len("é".encode()) - 2 + excess)
    monkeypatch.setattr(sys, "stdin", io.BytesIO(stdin))
    if excess:
        with pytest.raises(PratError, match="byte limit"):
            acquire_prompt(PromptSource("inline", "é"), tmp_path)
    else:
        assert acquire_prompt(PromptSource("inline", "é"), tmp_path) == stdin + "\n\né".encode()


def test_named_file_is_opened_nonblocking_and_handlers_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("prompt", encoding="utf-8")
    opened: list[int] = []
    real_open = os.open

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int
    ) -> int:
        opened.append(flags)
        return real_open(path, flags)

    previous = {chosen: signal.getsignal(chosen) for chosen in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr(os, "open", recording_open)
    assert acquire_prompt(PromptSource("file", str(prompt_file)), tmp_path) == b"prompt"
    assert opened == [os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)]
    assert {chosen: signal.getsignal(chosen) for chosen in previous} == previous


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_signal_during_validation_interrupts_and_restores_handlers(
    chosen: signal.Signals, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    original_validate = prompt_input._validate

    def interrupting_validate(value: bytes, label: str) -> bytes:
        os.kill(os.getpid(), chosen)
        return original_validate(value, label)

    monkeypatch.setattr(prompt_input, "_validate", interrupting_validate)
    with pytest.raises(InputInterrupted, match=f"Interrupted by signal {chosen.value}") as raised:
        acquire_prompt(PromptSource("inline", "prompt"), tmp_path)

    assert raised.value.signum == chosen
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous


@pytest.mark.parametrize("kind", ["terminal", "closed", "unavailable", "empty"])
def test_optional_base_accepts_absent_input(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.BytesIO(b"")
    if kind == "terminal":
        monkeypatch.setattr(stream, "isatty", lambda: True)
    elif kind == "closed":
        stream.close()
    monkeypatch.setattr(sys, "stdin", None if kind == "unavailable" else stream)
    assert acquire_prompt(None, tmp_path, allow_absent=True) == b""


@pytest.mark.parametrize("stdin", [b" \n", b"\xff", b"\0"])
def test_optional_base_still_validates_provided_implicit_input(
    stdin: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.BytesIO(stdin))
    with pytest.raises(PratError):
        acquire_prompt(None, tmp_path, allow_absent=True)


@pytest.mark.parametrize("path", ['notes "one"\\two\n雪😀\x7f.md', "-", "empty.md"])
def test_context_preserves_content_and_quotes_supplied_label(tmp_path: Path, path: str) -> None:
    content = b"" if path == "empty.md" else " \r\n$input 雪\r\n".encode()
    (tmp_path / path).write_bytes(content)
    expected_label = {
        'notes "one"\\two\n雪😀\x7f.md': b'"notes \\"one\\"\\\\two\\n\\u96ea\\ud83d\\ude00\\u007f.md"',
        "-": b'"-"',
        "empty.md": b'"empty.md"',
    }[path]
    assert prompt_input.prepend_contexts((path,), b"task", tmp_path) == (
        b"# Context: " + expected_label + b"\n\n" + content + b"\n\ntask"
    )


def test_context_order_duplicates_and_symlinks(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"one")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    assert prompt_input.prepend_contexts(("a", "b", "a"), b"task", tmp_path) == (
        b'# Context: "a"\n\none\n\n# Context: "b"\n\none\n\n# Context: "a"\n\none\n\ntask'
    )


@pytest.mark.parametrize("kind", ["context", "prompt"])
def test_file_parent_traversal_follows_directory_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    (tmp_path / "target" / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "target" / "sub", target_is_directory=True)
    (tmp_path / "notes.md").write_bytes(b"root notes")
    (tmp_path / "target" / "notes.md").write_bytes(b"target notes")
    path = "link/../notes.md"
    if kind == "context":
        assert prompt_input.prepend_contexts((path,), b"task", tmp_path) == (
            b'# Context: "link/../notes.md"\n\ntarget notes\n\ntask'
        )
    else:
        monkeypatch.setattr(sys, "stdin", io.BytesIO(b""))
        assert acquire_prompt(PromptSource("file", path), tmp_path) == b"target notes"


@pytest.mark.parametrize("content", [b"\xff", b"a\0b"])
def test_context_rejects_invalid_content(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "a").write_bytes(content)
    with pytest.raises(PratError, match=r"valid UTF-8|NUL"):
        prompt_input.prepend_contexts(("a",), b"task", tmp_path)


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_context_requires_regular_file(tmp_path: Path, kind: str) -> None:
    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(PratError, match=r"Cannot open context file|not a regular file"):
        prompt_input.prepend_contexts((kind,), b"task", tmp_path)


@pytest.mark.parametrize("excess", [0, 1])
def test_context_limit_includes_every_label_separator_and_task(tmp_path: Path, excess: int) -> None:
    (tmp_path / "a").write_bytes(b"first")
    prefix = b'# Context: "a"\n\nfirst\n\n# Context: "b"\n\n'
    task = "雪".encode()
    content = b"x" * (PROMPT_LIMIT - len(prefix) - 2 - len(task) + excess)
    (tmp_path / "b").write_bytes(content)
    if excess:
        with pytest.raises(PratError, match="byte limit"):
            prompt_input.prepend_contexts(("a", "b"), task, tmp_path)
    else:
        assert prompt_input.prepend_contexts(("a", "b"), task, tmp_path) == (
            prefix + content + b"\n\n" + task
        )


def test_context_label_overflow_fails_before_opening_files(tmp_path: Path) -> None:
    with pytest.raises(PratError, match="byte limit"):
        prompt_input.prepend_contexts(
            ("missing", "also-missing"), b"x" * (PROMPT_LIMIT - 30), tmp_path
        )


def test_context_budget_stops_reading_before_later_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").write_bytes(b"x" * PROMPT_LIMIT)
    reads: list[int] = []
    original_read = os.read

    def record_read(descriptor: int, size: int) -> bytes:
        reads.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", record_read)
    with pytest.raises(PratError, match="byte limit"):
        prompt_input.prepend_contexts(("a", "missing"), b"x" * (PROMPT_LIMIT - 50), tmp_path)
    assert reads == [9]


def test_combined_prompt_reads_only_remaining_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.BytesIO(b"x" * PROMPT_LIMIT)
    monkeypatch.setattr(sys, "stdin", stream)
    with pytest.raises(PratError, match="byte limit"):
        acquire_prompt(PromptSource("inline", "y" * (PROMPT_LIMIT - 3)), tmp_path)
    assert stream.tell() == 2


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_context_signal_interrupts_read_and_restores_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chosen: signal.Signals
) -> None:
    (tmp_path / "a").write_bytes(b"context")
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    original_read = os.read

    def interrupting_read(descriptor: int, size: int) -> bytes:
        os.kill(os.getpid(), chosen)
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", interrupting_read)
    with pytest.raises(InputInterrupted) as caught:
        prompt_input.prepend_contexts(("a",), b"task", tmp_path)
    assert caught.value.signum == chosen
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous
