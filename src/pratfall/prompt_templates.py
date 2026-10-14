from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from re import Match
from string import Template
from types import MappingProxyType

from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT, PromptSource

_INPUT = "input"


@dataclass(frozen=True)
class BoundInput:
    source: PromptSource | None
    parameters: Mapping[str, str]


def validate_template(prompt: str, label: str) -> None:
    for match in _matches(prompt):
        if match.group("invalid") is not None:
            raise PratError(
                f"{label}: use $name or ${{name}} placeholders and $$ for a literal dollar."
            )
        identifier = _identifier(match)
        if identifier is not None and identifier != _INPUT and identifier.lower() == _INPUT:
            raise PratError(
                f"{label}: placeholder '{identifier}' differs from 'input' only in case; "
                "use $input or ${input} for the base prompt."
            )
    if len(prompt.encode("utf-8")) > PROMPT_LIMIT:
        raise PratError(f"{label}: template exceeds the {PROMPT_LIMIT} byte limit.")


def template_parameters(prompt: str) -> tuple[str, ...]:
    identifiers = (_identifier(match) for match in _matches(prompt))
    return tuple(dict.fromkeys(name for name in identifiers if name is not None and name != _INPUT))


def bind_run_input(
    prompt: str | None,
    label: str,
    words: tuple[str, ...],
    named: tuple[str, ...],
    explicit: PromptSource | None,
) -> BoundInput:
    names = () if prompt is None else template_parameters(prompt)
    return bind_input(names, label, words, named, explicit, active=prompt is not None)


def bind_input(
    names: tuple[str, ...],
    label: str,
    words: tuple[str, ...],
    named: tuple[str, ...],
    explicit: PromptSource | None,
    *,
    active: bool,
) -> BoundInput:
    if named and not names:
        target = f"{label} has no parameters" if active else "no template is active"
        raise _argument_error(f"--param needs a prompt with parameters, but {target}.")
    if not names:
        return BoundInput(_plain_source(words, explicit), MappingProxyType({}))
    return BoundInput(explicit, MappingProxyType(_bind_parameters(names, label, words, named)))


def render_template(
    prompt: str,
    base: bytes,
    *,
    parameters: Mapping[str, str] | None = None,
    allow_blank: bool = False,
) -> bytes:
    values = dict(parameters or {})
    lengths = {name: len(value.encode("utf-8")) for name, value in values.items()}
    size = len(prompt.encode("utf-8"))
    interpolated = False
    for match in _matches(prompt):
        identifier = _identifier(match)
        if match.group("escaped") is not None:
            size -= 1
        elif identifier == _INPUT:
            interpolated = True
            size += len(base) - len(match.group().encode("utf-8"))
        elif identifier is not None:
            size += lengths[identifier] - len(match.group().encode("utf-8"))
    if not interpolated and base:
        size += 2 + len(base)
    if size > PROMPT_LIMIT:
        raise PratError(f"Prompt exceeds the {PROMPT_LIMIT} byte limit.", code="invalid_arguments")
    values[_INPUT] = base.decode("utf-8")
    rendered = Template(prompt).substitute(values).encode("utf-8")
    if not interpolated and base:
        rendered += b"\n\n" + base
    if not allow_blank and not rendered.decode("utf-8").strip():
        raise PratError("Prompt must not be empty or whitespace-only.", code="invalid_arguments")
    return rendered


def _matches(prompt: str) -> Iterator[Match[str]]:
    return Template.pattern.finditer(prompt)


def _identifier(match: Match[str]) -> str | None:
    return match.group("named") or match.group("braced")


def _plain_source(words: tuple[str, ...], explicit: PromptSource | None) -> PromptSource | None:
    if not words:
        return explicit
    if len(words) > 1 or explicit is not None:
        raise _argument_error("Provide exactly one prompt source.")
    return PromptSource("stdin") if words[0] == "-" else PromptSource("inline", words[0])


def _bind_parameters(
    names: tuple[str, ...], label: str, words: tuple[str, ...], named: tuple[str, ...]
) -> dict[str, str]:
    if len(words) > len(names):
        raise _argument_error(
            f"{label} takes {len(names)} parameter value(s) ({', '.join(names)}) but got "
            f"{len(words)}; pass free text with --prompt=TEXT or --file PATH."
        )
    bound: dict[str, str] = {}
    for name, word in zip(names, words, strict=False):
        if word == "-":
            raise _argument_error(
                f"{label}: '-' cannot be the value of parameter '{name}'; "
                f"use --param {name}=- for a literal dash or --file - to read standard input."
            )
        bound[name] = word
    for raw in named:
        name, value = _split_named(raw, names, label)
        if name in bound:
            raise _argument_error(f"{label}: parameter '{name}' is bound more than once.")
        bound[name] = value
    missing = [name for name in names if name not in bound]
    if missing:
        raise _argument_error(f"{label}: missing value for parameter(s) {', '.join(missing)}.")
    for name in names:
        _check_value(bound[name], name, label)
    return {name: bound[name] for name in names}


def _split_named(raw: str, names: tuple[str, ...], label: str) -> tuple[str, str]:
    name, separator, value = raw.partition("=")
    if not separator:
        raise _argument_error(f"{label}: --param '{raw}' must have the form NAME=VALUE.")
    if not name:
        raise _argument_error(f"{label}: --param '{raw}' has an empty parameter name.")
    if name == _INPUT:
        raise _argument_error(
            f"{label}: 'input' is not a parameter; provide the base prompt with "
            "--prompt=TEXT, --file PATH, or standard input."
        )
    if name not in names:
        raise _argument_error(
            f"{label} has no parameter '{name}'; its parameters are {', '.join(names)}."
        )
    return name, value


def _check_value(value: str, name: str, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _argument_error(f"{label}: parameter '{name}' is not valid UTF-8.") from error
    if "\0" in value:
        raise _argument_error(f"{label}: parameter '{name}' must not contain NUL bytes.")
    if not value.strip():
        raise _argument_error(f"{label}: parameter '{name}' must not be empty or whitespace-only.")


def _argument_error(message: str) -> PratError:
    return PratError(message, code="invalid_arguments")
