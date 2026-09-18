from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from pratfall.errors import PratError

_ECMASCRIPT_TRIM_CHARACTERS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


@dataclass(frozen=True)
class Flag:
    arity: int
    joined: bool = False
    variadic: bool = False


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
            fail_native_argument(agent, argument, "response files are not accepted")
        name, has_equals = _split_long(argument)
        spec = reserved.get(name)
        if spec is not None or _joined_match(argument, reserved) is not None:
            fail_native_argument(agent, argument, "this option is controlled by prat")
        if spec is None:
            spec = allowed.get(name)
        if spec is None:
            joined = _joined_match(argument, allowed)
            if joined is not None:
                index += 1
                continue
            if not argument.startswith("-") or argument == "-":
                fail_native_argument(
                    agent, argument, "native positional arguments and subcommands are not accepted"
                )
            fail_native_argument(agent, argument, "unknown native option")
        if has_equals:
            if spec.arity != 1 and not spec.variadic:
                fail_native_argument(agent, argument, "this option does not take a value")
            index = _consume_variadic(arguments, index + 1) if spec.variadic else index + 1
            continue
        if spec.variadic:
            index = _consume_variadic(arguments, index + 1)
            continue
        if spec.arity == 1:
            if index + 1 >= len(arguments):
                fail_native_argument(agent, argument, "missing native option value")
            if arguments[index + 1].startswith(("-", "@")):
                fail_native_argument(
                    agent, argument, "missing native option value; use the =VALUE form"
                )
            index += 2
        else:
            index += 1


def _consume_variadic(arguments: tuple[str, ...], index: int) -> int:
    while index < len(arguments) and not arguments[index].startswith(("-", "@")):
        index += 1
    return index


def _split_long(argument: str) -> tuple[str, bool]:
    if argument.startswith("--") and "=" in argument:
        return argument.split("=", 1)[0], True
    return argument, False


def _joined_match(argument: str, flags: Mapping[str, Flag]) -> str | None:
    for name, spec in flags.items():
        if spec.joined and argument.startswith(name) and argument != name:
            return name
    return None


def fail_native_argument(agent: str, argument: str, reason: str) -> NoReturn:
    raise PratError(
        f"{agent} native argument {argument!r}: {reason}; use a trusted executable wrapper "
        "for unsupported native arguments.",
        code="invalid_arguments",
    )


def validate_directory_values(paths: tuple[str, ...]) -> None:
    for value in paths:
        if "," in value or value != value.strip(_ECMASCRIPT_TRIM_CHARACTERS):
            raise PratError(
                f"Directory path {value!r} cannot contain commas or surrounding whitespace "
                "with this agent.",
                code="invalid_arguments",
                option="add_dirs",
            )


def validate_tool_values(
    values: tuple[str, ...] | None,
    option: str,
    *,
    comma: bool = False,
    whitespace: bool = False,
    trim: bool = False,
) -> None:
    for value in values or ():
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise PratError(
                "Tool names and patterns must be valid UTF-8.",
                code="invalid_arguments",
                option=option,
            ) from error
        if (
            (comma and "," in value)
            or (
                whitespace
                and any(char.isspace() or char in _ECMASCRIPT_TRIM_CHARACTERS for char in value)
            )
            or (trim and value != value.strip(_ECMASCRIPT_TRIM_CHARACTERS))
        ):
            raise PratError(
                f"Tool name or pattern {value!r} contains a native list delimiter "
                "or surrounding whitespace; use one native name or pattern per value.",
                code="invalid_arguments",
                option=option,
            )
