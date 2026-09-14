import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType

HANDLED_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)

Handler = Callable[[int, FrameType | None], None]
Previous = dict[signal.Signals, Handler | int | None]


@dataclass
class InterruptionState:
    received: int | None = None
    repeated: bool = False


def handler_for(
    state: InterruptionState,
    *,
    on_first: Callable[[int], None] | None = None,
    on_repeat: Callable[[], None] | None = None,
) -> Handler:
    def receive(signum: int, _frame: FrameType | None) -> None:
        if state.received is None:
            state.received = signum
            if on_first is not None:
                on_first(signum)
            return
        state.repeated = True
        if on_repeat is not None:
            on_repeat()

    return receive


def install(handler: Handler, previous: Previous) -> None:
    for chosen in HANDLED_SIGNALS:
        previous[chosen] = signal.getsignal(chosen)
        signal.signal(chosen, handler)


def restore(previous: Previous) -> None:
    for chosen, handler in previous.items():
        signal.signal(chosen, handler)


@contextmanager
def handling(handler: Handler) -> Iterator[None]:
    previous: Previous = {}
    try:
        install(handler, previous)
        yield
    finally:
        restore(previous)
