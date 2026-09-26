import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pratfall.models import NotifyMethod, Options

DEFAULT_NOTIFY_AFTER: float = 10.0
TTY_PATH: str = "/dev/tty"
Sequence = Literal["osc9", "osc777", "osc99", "bel"]

ESC = "\x1b"
BEL = "\x07"
KONSOLE_OSC777_VERSION = 230400
MINUTE = 60
HOUR = 3600

TERM_PROGRAM_SEQUENCES: Mapping[str, Sequence] = {
    "iTerm.app": "osc9",
    "ghostty": "osc777",
    "WezTerm": "osc777",
    "WarpTerminal": "osc777",
    "vscode": "osc99",
    "Apple_Terminal": "bel",
}

_UNSAFE = dict.fromkeys((*range(0x20), *range(0x7F, 0xA0)), " ") | {ord(";"): ","}


@dataclass(frozen=True)
class Notification:
    title: str
    body: str


def _duration(duration_ms: int) -> str:
    seconds = max(duration_ms, 0) // 1000
    if seconds < MINUTE:
        return f"{seconds}s"
    if seconds < HOUR:
        return f"{seconds // MINUTE}m {seconds % MINUTE}s"
    return f"{seconds // HOUR}h {seconds % HOUR // MINUTE}m"


def notification_for(agent_label: str, status: str, duration_ms: int) -> Notification:
    duration = _duration(duration_ms)
    if status == "success":
        body = f"{agent_label} done in {duration}"
    elif status == "timeout":
        body = f"{agent_label} timed out after {duration}"
    else:
        body = f"{agent_label} failed after {duration}"
    return Notification(title="prat", body=body)


def should_notify(options: Options, status: str, duration_ms: int, *, stderr_is_tty: bool) -> bool:
    threshold = DEFAULT_NOTIFY_AFTER if options.notify_after is None else options.notify_after
    return (
        options.notify is not False
        and stderr_is_tty
        and status != "interrupted"
        and duration_ms / 1000 >= threshold
    )


def _konsole_sequence(version: str) -> Sequence:
    try:
        parsed = int(version)
    except ValueError:
        return "bel"
    return "osc777" if parsed >= KONSOLE_OSC777_VERSION else "bel"


def detect_sequence(env: Mapping[str, str]) -> Sequence:
    if "ZELLIJ" in env:
        return "osc99"
    by_program = TERM_PROGRAM_SEQUENCES.get(env.get("TERM_PROGRAM", ""))
    if by_program is not None:
        return by_program
    term = env.get("TERM", "")
    konsole = env.get("KONSOLE_VERSION", "")
    rules: tuple[tuple[bool, Sequence], ...] = (
        (env.get("LC_TERMINAL") == "iTerm2" or bool(env.get("ITERM_SESSION_ID")), "osc9"),
        (bool(env.get("KITTY_WINDOW_ID")) or "kitty" in term, "osc99"),
        (bool(env.get("GHOSTTY_RESOURCES_DIR")) or term == "xterm-ghostty", "osc777"),
        (bool(env.get("WEZTERM_PANE") or env.get("WEZTERM_EXECUTABLE")), "osc777"),
        (term == "foot" or term.startswith("foot-"), "osc777"),
        (bool(konsole), _konsole_sequence(konsole)),
    )
    return next((sequence for matched, sequence in rules if matched), "bel")


def _sanitize(text: str) -> str:
    return text.translate(_UNSAFE)


def _sequence(notification: Notification, sequence: Sequence) -> str:
    title = _sanitize(notification.title)
    body = _sanitize(notification.body)
    if sequence == "osc9":
        return f"{ESC}]9;{title}: {body}{BEL}"
    if sequence == "osc777":
        return f"{ESC}]777;notify;{title};{body}{BEL}"
    if sequence == "osc99":
        return f"{ESC}]99;o=unfocused;{title}: {body}{BEL}"
    return BEL


def _wrap(sequence: str, env: Mapping[str, str]) -> str:
    if sequence == BEL:
        return sequence
    if env.get("TMUX"):
        return f"{ESC}Ptmux;{sequence.replace(ESC, ESC + ESC)}{ESC}\\"
    if env.get("STY"):
        return f"{ESC}P{sequence}{ESC}\\"
    return sequence


def encode(notification: Notification, method: NotifyMethod, env: Mapping[str, str]) -> bytes:
    sequence = detect_sequence(env) if method == "auto" else method
    return _wrap(_sequence(notification, sequence), env).encode()


def send(payload: bytes, path: str = TTY_PATH) -> None:
    try:
        previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    except ValueError:
        return
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    except OSError:
        return
    finally:
        if previous is not None:
            signal.signal(signal.SIGTTOU, previous)
