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
