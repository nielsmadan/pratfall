from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from pratfall.errors import PratError


@dataclass(frozen=True)
class Flag:
    arity: int
    joined: bool = False


def validate_flags(
    agent: str,
    arguments: tuple[str, ...],
    allowed: Mapping[str, Flag],
    reserved: Mapping[str, Flag],
) -> None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("@"):
            _fail(agent, argument, "response files are not accepted")
        name, has_equals = _split_long(argument)
        spec = reserved.get(name)
        if spec is not None or _joined_match(argument, reserved) is not None:
            _fail(agent, argument, "this option is controlled by prat")
        if spec is None:
            spec = allowed.get(name)
        if spec is None:
            joined = _joined_match(argument, allowed)
            if joined is not None:
                index += 1
                continue
            if not argument.startswith("-") or argument == "-":
                _fail(
                    agent, argument, "native positional arguments and subcommands are not accepted"
                )
            _fail(agent, argument, "unknown native option")
        if has_equals:
            if spec.arity != 1:
                _fail(agent, argument, "this option does not take a value")
            index += 1
            continue
        if spec.arity == 1:
            if index + 1 >= len(arguments):
                _fail(agent, argument, "missing native option value")
            if arguments[index + 1].startswith(("-", "@")):
                _fail(agent, argument, "missing native option value; use the =VALUE form")
            index += 2
        else:
            index += 1


def _split_long(argument: str) -> tuple[str, bool]:
    if argument.startswith("--") and "=" in argument:
        return argument.split("=", 1)[0], True
    return argument, False


def _joined_match(argument: str, flags: Mapping[str, Flag]) -> str | None:
    for name, spec in flags.items():
        if spec.joined and argument.startswith(name) and argument != name:
            return name
    return None


def _fail(agent: str, argument: str, reason: str) -> NoReturn:
    raise PratError(
        f"{agent} native argument {argument!r}: {reason}; use a trusted executable wrapper "
        "for unsupported native arguments.",
        code="invalid_arguments",
    )
