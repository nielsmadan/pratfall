import os
import signal
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest

from pratfall.models import NotifyMethod, Options
from pratfall.notify import (
    DEFAULT_NOTIFY_AFTER,
    Notification,
    Sequence,
    detect_sequence,
    encode,
    notification_for,
    send,
    should_notify,
)

NOTE = Notification(title="prat", body="Claude done in 42s")


@pytest.fixture(autouse=True)
def _restore_sigttou() -> Iterator[None]:
    original = signal.getsignal(signal.SIGTTOU)
    try:
        yield
    finally:
        signal.signal(signal.SIGTTOU, original)


def _sentinel(_signum: int, _frame: FrameType | None) -> None:
    raise AssertionError("test sentinel handler ran")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "bel"),
        ({"ZELLIJ": "0"}, "osc99"),
        ({"ZELLIJ": ""}, "osc99"),
        ({"ZELLIJ": "0", "TERM_PROGRAM": "iTerm.app"}, "osc99"),
        ({"TERM_PROGRAM": "iTerm.app"}, "osc9"),
        ({"TERM_PROGRAM": "ghostty"}, "osc777"),
        ({"TERM_PROGRAM": "WezTerm"}, "osc777"),
        ({"TERM_PROGRAM": "WarpTerminal"}, "osc777"),
        ({"TERM_PROGRAM": "vscode"}, "osc99"),
        ({"TERM_PROGRAM": "Apple_Terminal"}, "bel"),
        ({"TERM_PROGRAM": "Apple_Terminal", "ITERM_SESSION_ID": "w0t0p0"}, "bel"),
        ({"TERM_PROGRAM": "tmux", "KITTY_WINDOW_ID": "1"}, "osc99"),
        ({"TERM_PROGRAM": "unknown"}, "bel"),
        ({"LC_TERMINAL": "iTerm2"}, "osc9"),
        ({"ITERM_SESSION_ID": "w0t0p0"}, "osc9"),
        ({"ITERM_SESSION_ID": ""}, "bel"),
        ({"KITTY_WINDOW_ID": "1"}, "osc99"),
        ({"TERM": "xterm-kitty"}, "osc99"),
        ({"GHOSTTY_RESOURCES_DIR": "/opt/ghostty"}, "osc777"),
        ({"TERM": "xterm-ghostty"}, "osc777"),
        ({"WEZTERM_PANE": "0"}, "osc777"),
        ({"WEZTERM_EXECUTABLE": "/bin/wezterm"}, "osc777"),
        ({"TERM": "foot"}, "osc777"),
        ({"TERM": "foot-extra"}, "osc777"),
        ({"TERM": "footloose"}, "bel"),
        ({"KONSOLE_VERSION": "230400"}, "osc777"),
        ({"KONSOLE_VERSION": "240801"}, "osc777"),
        ({"KONSOLE_VERSION": "230399"}, "bel"),
        ({"KONSOLE_VERSION": "22.12"}, "bel"),
        ({"TERM": "xterm-256color", "VTE_VERSION": "7600"}, "bel"),
    ],
)
def test_detect_sequence_picks_the_first_matching_rule(
    env: dict[str, str], expected: Sequence
) -> None:
    assert detect_sequence(env) == expected


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("osc9", b"\x1b]9;prat: Claude done in 42s\x07"),
        ("osc777", b"\x1b]777;notify;prat;Claude done in 42s\x07"),
        ("osc99", b"\x1b]99;o=unfocused;prat: Claude done in 42s\x07"),
        ("bel", b"\x07"),
    ],
)
def test_encode_builds_each_sequence_without_a_multiplexer(
    method: NotifyMethod, expected: bytes
) -> None:
    assert encode(NOTE, method, {}) == expected


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("osc9", b"\x1bPtmux;\x1b\x1b]9;prat: Claude done in 42s\x07\x1b\\"),
        ("osc777", b"\x1bPtmux;\x1b\x1b]777;notify;prat;Claude done in 42s\x07\x1b\\"),
        ("osc99", b"\x1bPtmux;\x1b\x1b]99;o=unfocused;prat: Claude done in 42s\x07\x1b\\"),
        ("bel", b"\x07"),
    ],
)
def test_encode_wraps_osc_sequences_for_tmux(method: NotifyMethod, expected: bytes) -> None:
    assert encode(NOTE, method, {"TMUX": "tmux-1/default,1,0", "STY": "1.pts"}) == expected


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("osc9", b"\x1bP\x1b]9;prat: Claude done in 42s\x07\x1b\\"),
        ("osc777", b"\x1bP\x1b]777;notify;prat;Claude done in 42s\x07\x1b\\"),
        ("osc99", b"\x1bP\x1b]99;o=unfocused;prat: Claude done in 42s\x07\x1b\\"),
        ("bel", b"\x07"),
    ],
)
def test_encode_wraps_osc_sequences_for_screen(method: NotifyMethod, expected: bytes) -> None:
    assert encode(NOTE, method, {"STY": "1234.pts-0.host"}) == expected


def test_encode_leaves_zellij_unwrapped() -> None:
    assert (
        encode(NOTE, "auto", {"ZELLIJ": "0"}) == b"\x1b]99;o=unfocused;prat: Claude done in 42s\x07"
    )


def test_encode_auto_uses_the_detected_sequence() -> None:
    assert encode(NOTE, "auto", {"TERM_PROGRAM": "iTerm.app"}) == (
        b"\x1b]9;prat: Claude done in 42s\x07"
    )


def test_encode_uses_an_explicit_method_over_detection() -> None:
    assert encode(NOTE, "osc777", {"TERM_PROGRAM": "iTerm.app"}) == (
        b"\x1b]777;notify;prat;Claude done in 42s\x07"
    )


def test_encode_sanitizes_separators_and_control_characters() -> None:
    note = Notification(title="p;r\x7fat", body="a;b\nc\x1bd\x9ceéf")

    assert encode(note, "osc777", {}) == ("\x1b]777;notify;p,r at;a,b c d eéf\x07".encode())


@pytest.mark.parametrize(
    ("status", "duration_ms", "body"),
    [
        ("success", 42_000, "Claude done in 42s"),
        ("success", 59_999, "Claude done in 59s"),
        ("success", 60_000, "Claude done in 1m 0s"),
        ("success", 192_000, "Claude done in 3m 12s"),
        ("success", 3_599_000, "Claude done in 59m 59s"),
        ("success", 3_600_000, "Claude done in 1h 0m"),
        ("success", 3_900_000, "Claude done in 1h 5m"),
        ("timeout", 59_000, "Claude timed out after 59s"),
        ("failure", 60_000, "Claude failed after 1m 0s"),
        ("auth_failure", 3_600_000, "Claude failed after 1h 0m"),
    ],
)
def test_notification_for_words_the_status_and_duration(
    status: str, duration_ms: int, body: str
) -> None:
    assert notification_for("Claude", status, duration_ms) == Notification("prat", body)


def test_should_notify_fires_at_the_default_threshold() -> None:
    at_threshold = int(DEFAULT_NOTIFY_AFTER * 1000)

    assert should_notify(Options(), "success", at_threshold, stderr_is_tty=True) is True
    assert should_notify(Options(), "success", at_threshold - 1, stderr_is_tty=True) is False


def test_should_notify_is_off_when_disabled() -> None:
    assert should_notify(Options(notify=False), "success", 60_000, stderr_is_tty=True) is False
    assert should_notify(Options(notify=True), "success", 60_000, stderr_is_tty=True) is True


def test_should_notify_skips_interrupted_runs() -> None:
    assert should_notify(Options(), "interrupted", 60_000, stderr_is_tty=True) is False
    assert should_notify(Options(), "timeout", 60_000, stderr_is_tty=True) is True


def test_should_notify_requires_a_terminal_on_stderr() -> None:
    assert should_notify(Options(), "success", 60_000, stderr_is_tty=False) is False


def test_should_notify_honours_a_custom_threshold() -> None:
    options = Options(notify_after=2.5)

    assert should_notify(options, "success", 2_500, stderr_is_tty=True) is True
    assert should_notify(options, "success", 2_499, stderr_is_tty=True) is False


def test_should_notify_with_a_zero_threshold_fires_on_every_run() -> None:
    assert should_notify(Options(notify_after=0), "success", 0, stderr_is_tty=True) is True


def test_send_writes_the_exact_payload(tmp_path: Path) -> None:
    target = tmp_path / "tty"
    target.write_bytes(b"")

    send(b"\x1b]9;prat: done\x07", str(target))

    assert target.read_bytes() == b"\x1b]9;prat: done\x07"


def test_send_to_a_missing_path_returns_quietly(tmp_path: Path) -> None:
    send(b"\x07", str(tmp_path / "missing" / "tty"))


def test_send_ignores_sigttou_while_writing_and_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "tty"
    target.write_bytes(b"")
    signal.signal(signal.SIGTTOU, _sentinel)
    seen: list[object] = []
    real_open = os.open

    def recording_open(path: str, flags: int, mode: int = 0o777) -> int:
        seen.append(signal.getsignal(signal.SIGTTOU))
        return real_open(path, flags, mode)

    monkeypatch.setattr("pratfall.notify.os.open", recording_open)

    send(b"\x07", str(target))

    assert seen == [signal.SIG_IGN]
    assert signal.getsignal(signal.SIGTTOU) is _sentinel


def test_send_restores_sigttou_after_a_failed_open(tmp_path: Path) -> None:
    signal.signal(signal.SIGTTOU, _sentinel)

    send(b"\x07", str(tmp_path / "missing"))

    assert signal.getsignal(signal.SIGTTOU) is _sentinel


def test_send_closes_the_descriptor_and_swallows_a_failed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "tty"
    target.write_bytes(b"")
    signal.signal(signal.SIGTTOU, _sentinel)
    opened: list[int] = []
    closed: list[int] = []
    real_open = os.open
    real_close = os.close

    def recording_open(path: str, flags: int, mode: int = 0o777) -> int:
        descriptor = real_open(path, flags, mode)
        opened.append(descriptor)
        return descriptor

    def failing_write(_descriptor: int, _data: bytes) -> int:
        raise OSError("write failed")

    def recording_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr("pratfall.notify.os.open", recording_open)
    monkeypatch.setattr("pratfall.notify.os.write", failing_write)
    monkeypatch.setattr("pratfall.notify.os.close", recording_close)

    send(b"\x07", str(target))

    assert closed == opened
    assert signal.getsignal(signal.SIGTTOU) is _sentinel
