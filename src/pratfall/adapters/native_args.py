from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
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


_HELP: Mapping[str, Flag] = MappingProxyType(
    {"--help": Flag(0), "-h": Flag(0, joined=True), "--version": Flag(0)}
)


def validate_flags(
    agent: str,
    arguments: tuple[str, ...],
    allowed: Mapping[str, Flag],
    reserved: Mapping[str, Flag],
    *,
    attached_short_values: bool = True,
    single_dash_long: bool = False,
) -> None:
    reserved = {**_HELP, **reserved}
    index = 0
    unknown_flag: str | None = None
    while index < len(arguments):
        argument = arguments[index]
        previous, unknown_flag = unknown_flag, None
        if argument.startswith("@"):
            _fail(agent, argument, "response files are not accepted")
        if not argument.startswith("-") or argument in {"-", "--"}:
            _fail_positional(agent, argument, previous, equals=not attached_short_values)
        token = _long_form(argument) if single_dash_long else argument
        name, has_equals = _split_long(token)
        if _is_reserved(token, name, allowed, reserved, attached_short_values):
            fail_native_argument(agent, argument, "this option is controlled by prat")
        spec = allowed.get(name)
        if spec is None:
            if _joined_match(token, allowed) is None and not has_equals:
                unknown_flag = argument
            index += 1
            continue
        if has_equals:
            if spec.arity != 1 and not spec.variadic:
                _fail(agent, argument, "this option does not take a value")
            index = _consume_variadic(arguments, index + 1) if spec.variadic else index + 1
            continue
        if spec.variadic:
            index = _consume_variadic(arguments, index + 1)
            continue
        if spec.arity == 1:
            if index + 1 >= len(arguments):
                _fail(agent, argument, "missing native option value")
            if arguments[index + 1].startswith(("-", "@")):
                _fail(agent, argument, "missing native option value; use the =VALUE form")
            index += 2
        else:
            index += 1


def _long_form(argument: str) -> str:
    name = argument.split("=", 1)[0]
    return argument if argument.startswith("--") or len(name) <= len("-x") else "-" + argument


def _is_reserved(
    argument: str,
    name: str,
    allowed: Mapping[str, Flag],
    reserved: Mapping[str, Flag],
    attached_short_values: bool,
) -> bool:
    if name in reserved or _joined_match(argument, reserved) is not None:
        return True
    if name in allowed:
        return False
    if name.startswith("--"):
        return _joined_match(argument, allowed) is None and _long_reserved(name, reserved)
    return _cluster_reserved(argument, allowed, reserved, attached_short_values)


def _long_reserved(name: str, reserved: Mapping[str, Flag]) -> bool:
    kebab = "".join(f"-{char.lower()}" if char.isupper() else char for char in name)
    negated = "--" + kebab.removeprefix("--no-") if kebab.startswith("--no-") else None
    return negated in reserved or any(
        flag.startswith(spelling) for flag in reserved for spelling in (name, kebab)
    )


def _cluster_reserved(
    argument: str,
    allowed: Mapping[str, Flag],
    reserved: Mapping[str, Flag],
    attached_short_values: bool,
) -> bool:
    for letter in argument[1:]:
        if letter == "=":
            return False
        short = f"-{letter}"
        if short in reserved:
            return True
        spec = allowed.get(short)
        if attached_short_values and spec is not None and (spec.arity == 1 or spec.variadic):
            return False
    return False


def _fail_positional(agent: str, argument: str, previous: str | None, *, equals: bool) -> NoReturn:
    if previous is None:
        fail_native_argument(agent, argument, "positional arguments are not accepted")
    attached = not equals and not previous.startswith("--") and len(previous) == len("-x")
    joined = f"{previous}{argument}" if attached else f"{previous}={argument}"
    _fail(
        agent,
        argument,
        f"positional arguments are not accepted; if it is the value of {previous}, write {joined}",
    )


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
    _fail(
        agent,
        argument,
        f"{reason}; use a trusted executable wrapper for unsupported native arguments",
    )


def _fail(agent: str, argument: str, reason: str) -> NoReturn:
    raise PratError(f"{agent} native argument {argument!r}: {reason}", code="invalid_arguments")


def validate_directory_values(paths: tuple[str, ...]) -> None:
    for value in paths:
        if "," in value or value != value.strip(_ECMASCRIPT_TRIM_CHARACTERS):
            raise PratError(
                f"directory path {value!r} cannot contain commas or surrounding whitespace "
                "with this agent",
                code="invalid_arguments",
                option="add_dirs",
            )
