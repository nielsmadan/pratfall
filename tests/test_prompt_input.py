import os
import signal
from pathlib import Path

import pytest

from pratfall import prompt_input
from pratfall.prompt_input import InputInterrupted, PromptSource, acquire_prompt


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
