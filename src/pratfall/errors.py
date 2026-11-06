from collections.abc import Iterable
from difflib import get_close_matches

from pratfall.codes import Code, exit_code_for


class PratError(Exception):
    def __init__(
        self, message: str, *, code: Code = "invalid_config", option: str | None = None
    ) -> None:
        super().__init__(message)
        self.option = option
        self.code = code
        self.exit_code = exit_code_for(code)


def did_you_mean(value: str, candidates: Iterable[str], otherwise: str = "") -> str:
    matches = get_close_matches(value, sorted(set(candidates)), n=1)
    if matches:
        return f"; did you mean '{matches[0]}'?"
    return f"; {otherwise}" if otherwise else ""
