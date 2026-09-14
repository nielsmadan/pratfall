import signal
from collections.abc import Iterator
from types import FrameType

import pytest

from pratfall.interruption import (
    HANDLED_SIGNALS,
    InterruptionState,
    Previous,
    handler_for,
    handling,
    install,
    restore,
)


@pytest.fixture(autouse=True)
def _restore_process_handlers() -> Iterator[None]:
    original: Previous = {chosen: signal.getsignal(chosen) for chosen in HANDLED_SIGNALS}
    try:
        yield
    finally:
        restore(original)


def _sentinel(_signum: int, _frame: FrameType | None) -> None:
    raise AssertionError("test sentinel handler ran")


def test_install_records_and_replaces_the_previous_dispositions() -> None:
    for chosen in HANDLED_SIGNALS:
        signal.signal(chosen, _sentinel)
    state = InterruptionState()
    handler = handler_for(state)
    previous: Previous = {}

    install(handler, previous)

    assert previous == dict.fromkeys(HANDLED_SIGNALS, _sentinel)
    assert [signal.getsignal(chosen) for chosen in HANDLED_SIGNALS] == [handler, handler]
    restore(previous)
    assert [signal.getsignal(chosen) for chosen in HANDLED_SIGNALS] == [_sentinel, _sentinel]


def test_handling_restores_the_previous_dispositions_when_the_body_raises() -> None:
    for chosen in HANDLED_SIGNALS:
        signal.signal(chosen, _sentinel)
    state = InterruptionState()

    with pytest.raises(RuntimeError, match="body failed"), handling(handler_for(state)):
        assert [signal.getsignal(chosen) for chosen in HANDLED_SIGNALS] != [_sentinel, _sentinel]
        raise RuntimeError("body failed")

    assert [signal.getsignal(chosen) for chosen in HANDLED_SIGNALS] == [_sentinel, _sentinel]


def test_handler_records_the_first_signal_and_flags_repeats() -> None:
    state = InterruptionState()
    repeats = 0

    def on_repeat() -> None:
        nonlocal repeats
        repeats += 1

    handler = handler_for(state, on_repeat=on_repeat)
    handler(signal.SIGINT, None)
    assert (state.received, state.repeated, repeats) == (signal.SIGINT, False, 0)

    handler(signal.SIGTERM, None)
    assert (state.received, state.repeated, repeats) == (signal.SIGINT, True, 1)


def test_handler_without_hooks_still_flags_a_repeat() -> None:
    state = InterruptionState()
    handler = handler_for(state)

    handler(signal.SIGTERM, None)
    handler(signal.SIGTERM, None)

    assert (state.received, state.repeated) == (signal.SIGTERM, True)


def test_first_signal_hook_can_raise_out_of_the_handler() -> None:
    state = InterruptionState()

    def on_first(signum: int) -> None:
        raise RuntimeError(f"interrupted by {signum}")

    handler = handler_for(state, on_first=on_first)

    with pytest.raises(RuntimeError, match="interrupted by 2"):
        handler(signal.SIGINT, None)

    assert state.received == signal.SIGINT
