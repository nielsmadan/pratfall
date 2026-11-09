import io
import os
import signal
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from pratfall import prompt_input
from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT, InputInterrupted, PromptSource, acquire_prompt


class UntouchableStdin:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"standard input was used: {name}")


def untouchable() -> BinaryIO:
    return cast(BinaryIO, UntouchableStdin())


@pytest.mark.parametrize("kind", ["inline", "file"])
def test_explicit_prompt_neither_reads_nor_inspects_stdin(tmp_path: Path, kind: str) -> None:
    (tmp_path / "prompt.md").write_bytes(b"times 5")
    source = (
        PromptSource("inline", "times 5") if kind == "inline" else PromptSource("file", "prompt.md")
    )
    assert acquire_prompt(source, tmp_path, untouchable()) == b"times 5"


@pytest.mark.parametrize("kind", ["terminal", "closed", "unavailable"])
def test_inline_prompt_works_without_redirected_stdin(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.BytesIO(b"terminal input")
    if kind == "terminal":
        monkeypatch.setattr(stream, "isatty", lambda: True)
    elif kind == "closed":
        stream.close()
    stdin_stream = None if kind == "unavailable" else stream
    assert acquire_prompt(PromptSource("inline", "prompt"), tmp_path, stdin_stream) == b"prompt"
    if kind == "terminal":
        assert stream.tell() == 0


STDIN_HEADER = b"# Context: stdin\n\n"


@pytest.mark.parametrize("stdin", [b"diff\n", "café\n雪\n".encode(), b" \n", b""])
def test_stdin_context_is_a_block_headed_stdin(tmp_path: Path, stdin: bytes) -> None:
    assert prompt_input.prepend_contexts(("-",), b"review", tmp_path, io.BytesIO(stdin)) == (
        STDIN_HEADER + stdin + b"\n\nreview"
    )


def test_stdin_context_keeps_argument_order(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"one")
    stdin = io.BytesIO(b"piped")
    assert prompt_input.prepend_contexts(("a", "-", "a"), b"task", tmp_path, stdin) == (
        b'# Context: "a"\n\none\n\n' + STDIN_HEADER + b'piped\n\n# Context: "a"\n\none\n\ntask'
    )


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        (b"\xff", "standard input context is not valid UTF-8"),
        (b"x\0y", "NUL"),
        (b"x" * PROMPT_LIMIT, "byte limit"),
    ],
)
def test_stdin_context_rejects_invalid_input(tmp_path: Path, stdin: bytes, message: str) -> None:
    with pytest.raises(PratError, match=message):
        prompt_input.prepend_contexts(("-",), b"task", tmp_path, io.BytesIO(stdin))


def test_stdin_context_without_stdin_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(PratError) as raised:
        prompt_input.prepend_contexts(("-",), b"task", tmp_path, None)
    assert str(raised.value) == "cannot read standard input"


@pytest.mark.parametrize("excess", [0, 1])
def test_stdin_context_limit_includes_header_separator_and_task(
    tmp_path: Path, excess: int
) -> None:
    task = "é".encode()
    stdin = b"x" * (PROMPT_LIMIT - len(STDIN_HEADER) - 2 - len(task) + excess)
    if excess:
        with pytest.raises(PratError, match="byte limit"):
            prompt_input.prepend_contexts(("-",), task, tmp_path, io.BytesIO(stdin))
    else:
        assert prompt_input.prepend_contexts(("-",), task, tmp_path, io.BytesIO(stdin)) == (
            STDIN_HEADER + stdin + b"\n\n" + task
        )


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
    assert acquire_prompt(PromptSource("file", str(prompt_file)), tmp_path, None) == b"prompt"
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

    def interrupting_validate(value: bytes, label: str, *, allow_empty: bool = False) -> bytes:
        os.kill(os.getpid(), chosen)
        return original_validate(value, label, allow_empty=allow_empty)

    monkeypatch.setattr(prompt_input, "_validate", interrupting_validate)
    with pytest.raises(InputInterrupted, match=f"interrupted by signal {chosen.value}") as raised:
        acquire_prompt(PromptSource("inline", "prompt"), tmp_path, None)

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
    stdin_stream = None if kind == "unavailable" else stream
    assert acquire_prompt(None, tmp_path, stdin_stream, allow_absent=True) == b""


@pytest.mark.parametrize("stdin", [b" \n", b"\xff", b"\0"])
def test_optional_base_still_validates_provided_implicit_input(
    stdin: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdin_stream = io.BytesIO(stdin)
    with pytest.raises(PratError):
        acquire_prompt(None, tmp_path, stdin_stream, allow_absent=True)


@pytest.mark.parametrize("path", ['notes "one"\\two\n雪😀\x7f.md', "./-", "empty.md"])
def test_context_preserves_content_and_quotes_supplied_label(tmp_path: Path, path: str) -> None:
    content = b"" if path == "empty.md" else " \r\n$input 雪\r\n".encode()
    (tmp_path / path).write_bytes(content)
    expected_label = {
        'notes "one"\\two\n雪😀\x7f.md': b'"notes \\"one\\"\\\\two\\n\\u96ea\\ud83d\\ude00\\u007f.md"',
        "./-": b'"./-"',
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
        stdin_stream = io.BytesIO(b"")
        assert acquire_prompt(PromptSource("file", path), tmp_path, stdin_stream) == b"target notes"


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
    with pytest.raises(PratError, match=r"cannot open context file|not a regular file"):
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


def test_stdin_context_reads_only_remaining_budget(tmp_path: Path) -> None:
    stream = io.BytesIO(b"x" * PROMPT_LIMIT)
    task = b"y" * (PROMPT_LIMIT - len(STDIN_HEADER) - 2 - 1)
    with pytest.raises(PratError, match="byte limit"):
        prompt_input.prepend_contexts(("-",), task, tmp_path, stream)
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


@pytest.mark.parametrize("value", [b"", b" \n\t"])
@pytest.mark.parametrize("kind", ["stdin", "inline", "file"])
def test_edit_draft_allows_blank_sources(tmp_path: Path, value: bytes, kind: str) -> None:
    source: PromptSource
    stdin_stream = io.BytesIO(value if kind == "stdin" else b"")
    if kind == "stdin":
        source = PromptSource("stdin")
    elif kind == "file":
        (tmp_path / "draft.md").write_bytes(value)
        source = PromptSource("file", "draft.md")
    else:
        source = PromptSource("inline", value.decode())
    assert (
        acquire_prompt(source, tmp_path, stdin_stream, allow_absent=True, allow_blank=True) == value
    )
