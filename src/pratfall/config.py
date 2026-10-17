import math
import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, NamedTuple

from pratfall.catalog import BY_NAME, BY_SELECTOR, RESERVED_NAMES, get_agent
from pratfall.errors import PratError
from pratfall.instructions import validate_instructions
from pratfall.limits import (
    CONFIG_BYTES,
    DEFAULT_MAX_PARALLEL,
    MAX_PARALLEL_LIMIT,
    WORKFLOW_EACH_ITEMS,
)
from pratfall.local_trust import TrustState, digest, trust, trust_state
from pratfall.models import (
    MAX_TURNS,
    NOTIFY_METHODS,
    AgentSpec,
    Config,
    NotifyMethod,
    OptionOrigin,
    Options,
    Profile,
    ProfileLayer,
    PromptTemplate,
    ResolvedProfile,
    Workflow,
    WorkflowStep,
)
from pratfall.option_paths import resolve_path
from pratfall.prompt_templates import template_parameters, validate_template

DEFAULT_TIMEOUT = 600.0
OPTION_FIELDS = frozenset(Options.__dataclass_fields__)
SAMPLE_CONFIG = """version = 1

[defaults]
timeout = 600

[agents.codex]
command = ["codex"]

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
"""


def config_path(explicit: str | Path | None = None, *, cwd: Path, env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_CONFIG_HOME")
    try:
        if explicit is not None:
            path = Path(explicit).expanduser()
        else:
            xdg_root = Path(xdg or "").expanduser()
            root = xdg_root if xdg_root.is_absolute() else _home(env) / ".config"
            path = root / "pratfall" / "config.toml"
    except RuntimeError as error:
        requested = explicit if explicit is not None else xdg
        raise PratError(
            f"{requested or 'config path'}: cannot expand user path ({error}); "
            "use an absolute path or a valid ~user path."
        ) from error
    return Path(os.path.abspath(cwd / path))


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path.home()


def _table(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PratError(f"{label}: expected a TOML table.")
    return value


def _known_keys(table: dict[str, object], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(table.keys() - allowed)
    if unknown:
        raise PratError(f"{label}: unknown field {unknown[0]!r}.")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise PratError(f"{label}: expected a nonempty string without NUL bytes.")
    return value


def _validated_text(value: object, origin: OptionOrigin, noun: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise _rejected(origin, f"expected a nonempty {noun} without NUL bytes.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _rejected(origin, f"{noun} must be valid UTF-8.") from error
    return value


def validate_native_agent(value: object, origin: OptionOrigin) -> str:
    return _validated_text(value, origin, "native agent name")


def validate_session_id(value: object, origin: OptionOrigin) -> str:
    return _validated_text(value, origin, "session id")


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PratError(f"{label}: expected a finite positive number.")
    try:
        number = float(value)
    except OverflowError:
        raise PratError(f"{label}: expected a finite positive number.") from None
    if not math.isfinite(number) or number <= 0:
        raise PratError(f"{label}: expected a finite positive number.")
    return number


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_TURNS:
        raise PratError(f"{label}: expected a positive integer within the native u32 range.")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise PratError(f"{label}: expected a boolean.")
    return value


def _nonnegative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PratError(f"{label}: expected a finite non-negative number.")
    try:
        number = float(value)
    except OverflowError:
        raise PratError(f"{label}: expected a finite non-negative number.") from None
    if not math.isfinite(number) or number < 0:
        raise PratError(f"{label}: expected a finite non-negative number.")
    return number


def _notify_method(value: object, label: str) -> NotifyMethod:
    if not isinstance(value, str) or value not in NOTIFY_METHODS:
        allowed = ", ".join(NOTIFY_METHODS)
        raise PratError(f"{label}: expected one of {allowed}.")
    return value


def _arguments(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise PratError(f"{label}: expected an array of strings.")
    if any(not isinstance(item, str) or "\0" in item for item in value):
        raise PratError(f"{label}: expected an array of strings without NUL bytes.")
    return tuple(value)


def _paths(value: object, label: str, base: Path | None) -> tuple[str, ...]:
    paths = tuple(_string(item, label) for item in _arguments(value, label))
    if base is None:
        return paths
    return tuple(resolve_path(item, base, OptionOrigin(label)) for item in paths)


def parse_options(table: dict[str, object], label: str, *, base: Path | None = None) -> Options:
    _known_keys(table, OPTION_FIELDS, label)
    if "instructions" in table and "instructions_file" in table:
        raise PratError(f"{label}: instructions and instructions_file cannot be used together.")
    instructions_file = (
        _string(table["instructions_file"], f"{label}.instructions_file")
        if "instructions_file" in table
        else None
    )
    if instructions_file is not None and base is not None:
        instructions_file = resolve_path(
            instructions_file, base, OptionOrigin(f"{label}.instructions_file")
        )
    schema = _string(table["schema"], f"{label}.schema") if "schema" in table else None
    if schema is not None and base is not None:
        schema = resolve_path(schema, base, OptionOrigin(f"{label}.schema"))
    return Options(
        schema=schema,
        instructions=(
            validate_instructions(table["instructions"], OptionOrigin(f"{label}.instructions"))
            if "instructions" in table
            else None
        ),
        instructions_file=instructions_file,
        model=_string(table["model"], f"{label}.model") if "model" in table else None,
        effort=_string(table["effort"], f"{label}.effort") if "effort" in table else None,
        timeout=_number(table["timeout"], f"{label}.timeout") if "timeout" in table else None,
        max_budget_usd=(
            _number(table["max_budget_usd"], f"{label}.max_budget_usd")
            if "max_budget_usd" in table
            else None
        ),
        max_turns=(
            _integer(table["max_turns"], f"{label}.max_turns") if "max_turns" in table else None
        ),
        max_ai_credits=(
            _number(table["max_ai_credits"], f"{label}.max_ai_credits")
            if "max_ai_credits" in table
            else None
        ),
        fast=_boolean(table["fast"], f"{label}.fast") if "fast" in table else None,
        add_dirs=_paths(table["add_dirs"], f"{label}.add_dirs", base)
        if "add_dirs" in table
        else None,
        attachments=_paths(table["attachments"], f"{label}.attachments", base)
        if "attachments" in table
        else None,
        tools=tuple(
            _string(item, f"{label}.tools") for item in _arguments(table["tools"], f"{label}.tools")
        )
        if "tools" in table
        else None,
        disabled_tools=tuple(
            _string(item, f"{label}.disabled_tools")
            for item in _arguments(table["disabled_tools"], f"{label}.disabled_tools")
        )
        if "disabled_tools" in table
        else None,
        native_agent=(
            validate_native_agent(table["native_agent"], OptionOrigin(f"{label}.native_agent"))
            if "native_agent" in table
            else None
        ),
        session_id=(
            validate_session_id(table["session_id"], OptionOrigin(f"{label}.session_id"))
            if "session_id" in table
            else None
        ),
        native_args=(
            _arguments(table["native_args"], f"{label}.native_args")
            if "native_args" in table
            else None
        ),
        notify=_boolean(table["notify"], f"{label}.notify") if "notify" in table else None,
        notify_method=(
            _notify_method(table["notify_method"], f"{label}.notify_method")
            if "notify_method" in table
            else None
        ),
        notify_after=(
            _nonnegative_number(table["notify_after"], f"{label}.notify_after")
            if "notify_after" in table
            else None
        ),
        command_prefix=(
            _command_prefix(table["command_prefix"], f"{label}.command_prefix", base)
            if "command_prefix" in table
            else None
        ),
    )


def merge_options(*layers: Options) -> Options:
    values: dict[str, object] = {}
    for layer in layers:
        values = _without_instruction_peer(values, layer) | {
            key: value for key, value in asdict(layer).items() if value is not None
        }
    return parse_options(values, "options")


def _without_instruction_peer[T](values: Mapping[str, T], layer: Options) -> dict[str, T]:
    replaced = {
        peer
        for field, peer in (
            ("instructions", "instructions_file"),
            ("instructions_file", "instructions"),
        )
        if getattr(layer, field) is not None
    }
    return {key: value for key, value in values.items() if key not in replaced}


def _rejected(origin: OptionOrigin, message: str) -> PratError:
    return PratError(f"{origin.label}: {message}", code=origin.code)


def validate_capabilities(
    agent: AgentSpec,
    options: Options,
    label: str,
    *,
    origins: Mapping[str, OptionOrigin] | None = None,
) -> None:
    fields = {name: OptionOrigin(f"{label}.{name}") for name in OPTION_FIELDS}
    if origins is not None:
        fields.update(origins)
    caps = agent.capabilities
    for name, setting in (
        ("model", "a model override"),
        ("schema", "schema output"),
        ("native_agent", "native agent selection"),
        ("session_id", "a session id"),
        ("fast", "a fast-mode override"),
    ):
        if getattr(options, name) is not None and not getattr(caps, name):
            raise _rejected(fields[name], f"{agent.label} does not support {setting}.")
    if options.effort is not None:
        if not caps.effort:
            raise _rejected(fields["effort"], f"{agent.label} does not support an effort override.")
        if caps.effort_values and options.effort not in caps.effort_values:
            allowed = ", ".join(caps.effort_values)
            raise _rejected(fields["effort"], f"{agent.label} accepts: {allowed}.")
    for field in ("max_budget_usd", "max_turns", "max_ai_credits"):
        if getattr(options, field) is not None and field not in caps.budgets:
            raise _rejected(fields[field], f"{agent.label} does not support this budget.")
    if options.add_dirs and not caps.add_dirs:
        raise _rejected(fields["add_dirs"], f"{agent.label} does not support extra directories.")
    _validate_attachments(agent, options, fields["attachments"])
    for name in ("instructions", "instructions_file"):
        if getattr(options, name) is not None and not caps.instructions:
            raise _rejected(fields[name], f"{agent.label} does not support appended instructions.")
    if options.tools is not None:
        if not caps.tools:
            raise _rejected(fields["tools"], f"{agent.label} does not support tool allowlists.")
        if not options.tools and not caps.tools_empty:
            raise _rejected(
                fields["tools"], f"{agent.label} cannot represent an empty tool allowlist."
            )
    if options.disabled_tools and not caps.disabled_tools:
        raise _rejected(
            fields["disabled_tools"], f"{agent.label} does not support disabling tools."
        )


def _validate_attachments(agent: AgentSpec, options: Options, origin: OptionOrigin) -> None:
    caps = agent.capabilities
    if options.attachments:
        if not caps.attachments:
            raise _rejected(origin, f"{agent.label} does not support attachments.")
        if (
            caps.attachment_max_count is not None
            and len(options.attachments) > caps.attachment_max_count
        ):
            raise _rejected(
                origin,
                f"{agent.label} accepts at most {caps.attachment_max_count} attachment(s).",
            )


def option_origins(
    config: Config, selector: str, overrides: Options | None = None
) -> dict[str, OptionOrigin]:
    origins = {
        name: OptionOrigin(f"{source}: defaults.{name} (selector {selector!r})")
        for name, source in config.default_sources.items()
    }
    profile = config.profiles.get(selector)
    if profile is not None:
        for layer in profile.layers:
            origins = _without_instruction_peer(origins, layer.options) | {
                name: _layer_origin(layer, selector, name)
                for name, value in asdict(layer.options).items()
                if value is not None
            }
    if overrides is not None:
        origins = _without_instruction_peer(origins, overrides) | {
            name: OptionOrigin(f"command line: selector {selector!r}.{name}", "invalid_arguments")
            for name, value in asdict(overrides).items()
            if value is not None
        }
    return origins


def _with_executable(argv: tuple[str, ...], base: Path | None) -> tuple[str, ...]:
    executable = argv[0]
    if base is not None and "/" in executable and not Path(executable).is_absolute():
        executable = str(Path(os.path.abspath(base / executable)))
    return (executable, *argv[1:])


def _command_prefix(value: object, label: str, base: Path | None) -> tuple[str, ...]:
    prefix = _arguments(value, label)
    if not prefix:
        return prefix
    if not prefix[0].strip():
        raise PratError(f"{label}: expected an empty array or an executable argv prefix.")
    return _with_executable(prefix, base)


def _agent_settings(
    table: dict[str, object], path: Path
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    commands: dict[str, tuple[str, ...]] = {}
    prefixes: dict[str, tuple[str, ...]] = {}
    for name, value in table.items():
        label = f"{path}: agents.{name}"
        if name not in BY_NAME:
            raise PratError(f"{label}: use a canonical name from 'prat agents'.")
        settings = _table(value, label)
        _known_keys(settings, frozenset({"command", "command_prefix"}), label)
        if "command_prefix" in settings:
            prefixes[name] = _command_prefix(
                settings["command_prefix"], f"{label}.command_prefix", path.parent
            )
        if "command" not in settings:
            continue
        command = _arguments(settings["command"], f"{label}.command")
        if not command or not command[0].strip():
            raise PratError(f"{label}.command: expected a nonempty executable argv prefix.")
        commands[name] = _with_executable(command, path.parent)
    return commands, prefixes


def _declared_agent(value: object, label: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    name = _string(value, f"{label}.agent")
    try:
        return get_agent(name).name
    except PratError as error:
        raise PratError(f"{label}.agent: {error}", code=error.code) from error


def _profiles(table: dict[str, object], path: Path) -> dict[str, ProfileLayer]:
    layers: dict[str, ProfileLayer] = {}
    for name, value in table.items():
        label = f"{path}: profiles.{name}"
        if name in RESERVED_NAMES:
            raise PratError(f"{label}: name is reserved; choose a different profile name.")
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name) is None:
            raise PratError(
                f"{label}: use letters, digits, underscores or hyphens in profile names."
            )
        settings = _table(value, label)
        _known_keys(settings, OPTION_FIELDS | {"agent", "extends", "prompt"}, label)
        extends = (
            _string(settings["extends"], f"{label}.extends") if "extends" in settings else None
        )
        agent = _declared_agent(settings.get("agent"), label, required=extends is None)
        prompt = _profile_prompt(settings, label)
        options = parse_options(
            {
                key: val
                for key, val in settings.items()
                if key not in ("agent", "extends", "prompt")
            },
            label,
            base=path.parent,
        )
        layers[name] = ProfileLayer(
            name=name, agent=agent, options=options, source=path, extends=extends, prompt=prompt
        )
    return layers


def _profile_prompt(settings: dict[str, object], label: str) -> str | None:
    if "prompt" not in settings:
        return None
    prompt = _string(settings["prompt"], f"{label}.prompt")
    validate_template(prompt, f"{label}.prompt")
    return prompt


def _chain(layers: Mapping[str, ProfileLayer], name: str) -> tuple[ProfileLayer, ...]:
    chain: list[ProfileLayer] = []
    visited: list[str] = []
    seen: set[str] = set()
    current = name
    while True:
        layer = layers[current]
        chain.append(layer)
        visited.append(current)
        seen.add(current)
        if layer.extends is None:
            return tuple(reversed(chain))
        label = f"{layer.source}: profiles.{layer.name}.extends"
        if layer.extends not in layers:
            raise PratError(f"{label}: unknown profile {layer.extends!r}.")
        if layer.extends in seen:
            raise PratError(
                f"{label}: inheritance cycle: {' -> '.join((*visited, layer.extends))}."
            )
        current = layer.extends


def _resolve_layer(layers: Mapping[str, ProfileLayer], name: str) -> Profile:
    chain = _chain(layers, name)
    agent = next(declared for layer in reversed(chain) if (declared := layer.agent) is not None)
    return Profile(
        agent=agent,
        options=merge_options(*(layer.options for layer in chain)),
        source=chain[-1].source,
        layers=chain,
        prompt=next(
            (
                PromptTemplate(prompt=declared_prompt, source=layer.source)
                for layer in reversed(chain)
                if (declared_prompt := layer.prompt) is not None
            ),
            None,
        ),
    )


def _resolve_layers(layers: Mapping[str, ProfileLayer]) -> dict[str, Profile]:
    return {name: _resolve_layer(layers, name) for name in layers}


def _layer_origin(layer: ProfileLayer, selector: str, field: str) -> OptionOrigin:
    label = f"{layer.source}: profiles.{layer.name}.{field}"
    return OptionOrigin(label if layer.name == selector else f"{label} (selector {selector!r})")


def _templates(table: dict[str, object], path: Path) -> dict[str, PromptTemplate]:
    templates: dict[str, PromptTemplate] = {}
    for name, value in table.items():
        label = f"{path}: templates.{name}"
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name) is None:
            raise PratError(
                f"{label}: use letters, digits, underscores or hyphens in template names."
            )
        settings = _table(value, label)
        _known_keys(settings, frozenset({"prompt"}), label)
        prompt = _string(settings.get("prompt"), f"{label}.prompt")
        validate_template(prompt, f"{label}.prompt")
        templates[name] = PromptTemplate(prompt=prompt, source=path)
    return templates


class _EachSettings(NamedTuple):
    each: str | None = None
    split: Literal["lines", "json"] = "lines"
    as_parameter: str | None = None
    max_parallel: int = DEFAULT_MAX_PARALLEL
    max_items: int = WORKFLOW_EACH_ITEMS
    overflow: Literal["error", "truncate"] = "error"


def _bounded_integer(value: object, ceiling: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        return None
    return value


def _max_parallel(settings: dict[str, object], step_label: str) -> int:
    if "max_parallel" not in settings:
        return DEFAULT_MAX_PARALLEL
    value = settings["max_parallel"]
    if value == "all":
        return MAX_PARALLEL_LIMIT
    parallel = _bounded_integer(value, MAX_PARALLEL_LIMIT)
    if parallel is None:
        raise PratError(f'{step_label}.max_parallel: expected an integer from 1 to 64 or "all".')
    return parallel


def _max_items(settings: dict[str, object], step_label: str) -> int:
    if "max_items" not in settings:
        return WORKFLOW_EACH_ITEMS
    items = _bounded_integer(settings["max_items"], WORKFLOW_EACH_ITEMS)
    if items is None:
        raise PratError(
            f"{step_label}.max_items: expected an integer from 1 to {WORKFLOW_EACH_ITEMS}."
        )
    return items


def _overflow(settings: dict[str, object], step_label: str) -> Literal["error", "truncate"]:
    match settings.get("overflow", "error"):
        case "error":
            return "error"
        case "truncate":
            return "truncate"
        case _:
            raise PratError(f'{step_label}.overflow: expected "error" or "truncate".')


def _each_settings(settings: dict[str, object], step_label: str, no_input: bool) -> _EachSettings:
    if "each" not in settings:
        for key in ("split", "as", "max_parallel", "max_items", "overflow"):
            if key in settings:
                raise PratError(f"{step_label}.{key}: only valid together with each.")
        return _EachSettings()
    each = _string(settings["each"], f"{step_label}.each")
    split: Literal["lines", "json"] = "lines"
    if "split" in settings:
        if settings["split"] == "lines":
            split = "lines"
        elif settings["split"] == "json":
            split = "json"
        else:
            raise PratError(f'{step_label}.split: expected "lines" or "json".')
    as_parameter = _string(settings["as"], f"{step_label}.as") if "as" in settings else None
    max_parallel = _max_parallel(settings, step_label)
    max_items = _max_items(settings, step_label)
    overflow = _overflow(settings, step_label)
    if no_input and as_parameter is None:
        raise PratError(
            f'{step_label}.input: "none" needs as when each is set; the item is the input.'
        )
    return _EachSettings(each, split, as_parameter, max_parallel, max_items, overflow)


def _workflow_step(
    value: object, label: str, index: int, previous: tuple[WorkflowStep, ...]
) -> WorkflowStep:
    settings = _table(value, f"{label}.steps[{index}]")
    name = _string(settings.get("name"), f"{label}.steps[{index}].name")
    step_label = f"{label} step {name!r}"
    _known_keys(
        settings,
        frozenset(
            {
                "name",
                "profile",
                "after",
                "input",
                "bind",
                "each",
                "split",
                "as",
                "max_parallel",
                "max_items",
                "overflow",
            }
        ),
        step_label,
    )
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) is None:
        raise PratError(f"{step_label}: use letters, digits, underscores or hyphens in step names.")
    if any(step.name == name for step in previous):
        raise PratError(f"{step_label}: duplicate step name.")
    selector = _string(settings.get("profile"), f"{step_label}.profile")
    declared = {step.name for step in previous}
    if "after" in settings:
        after = tuple(
            _string(item, f"{step_label}.after")
            for item in _arguments(settings["after"], f"{step_label}.after")
        )
        if len(set(after)) != len(after):
            raise PratError(f"{step_label}.after: duplicate step names.")
        for item in after:
            if item not in declared:
                raise PratError(
                    f"{step_label}.after: {item!r} is not a step declared earlier in this workflow."
                )
    else:
        after = (previous[-1].name,) if previous else ()
    no_input = settings.get("input") == "none"
    if "input" in settings and not no_input:
        raise PratError(f'{step_label}.input: expected "none".')
    bind_table = _table(settings.get("bind", {}), f"{step_label}.bind")
    bind = {
        _string(key, f"{step_label}.bind"): _string(source, f"{step_label}.bind.{key}")
        for key, source in bind_table.items()
    }
    each = _each_settings(settings, step_label, no_input)
    return WorkflowStep(
        name=name,
        selector=selector,
        after=after,
        input="none" if no_input else "upstream",
        bind=MappingProxyType(bind),
        each=each.each,
        split=each.split,
        as_parameter=each.as_parameter,
        max_parallel=each.max_parallel,
        max_items=each.max_items,
        overflow=each.overflow,
    )


def _workflows(table: dict[str, object], path: Path) -> dict[str, Workflow]:
    workflows: dict[str, Workflow] = {}
    for name, value in table.items():
        label = f"{path}: workflows.{name}"
        if name in RESERVED_NAMES:
            raise PratError(f"{label}: name is reserved; choose a different workflow name.")
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name) is None:
            raise PratError(
                f"{label}: use letters, digits, underscores or hyphens in workflow names."
            )
        settings = _table(value, label)
        _known_keys(settings, frozenset({"steps", "parallel"}), label)
        raw_steps = settings.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PratError(f"{label}.steps: expected a nonempty array of step tables.")
        steps: tuple[WorkflowStep, ...] = ()
        for index, raw in enumerate(raw_steps):
            steps = (*steps, _workflow_step(raw, f"{path}: workflow {name!r}", index, steps))
        workflows[name] = Workflow(
            steps=steps,
            source=path,
            parallel=_boolean(settings["parallel"], f"{label}.parallel")
            if "parallel" in settings
            else True,
        )
    return workflows


def _ancestors(workflow: Workflow, step: WorkflowStep) -> frozenset[str]:
    by_name = {item.name: item for item in workflow.steps}
    found: set[str] = set()
    pending = list(step.after)
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(by_name[current].after)
    return frozenset(found)


def step_prompt(config: Config, step: WorkflowStep) -> str | None:
    profile = config.profiles.get(step.selector)
    template = None if profile is None else profile.prompt
    return None if template is None else template.prompt


def _validate_each(
    workflow: Workflow,
    step: WorkflowStep,
    label: str,
    ancestors: frozenset[str],
    parameters: tuple[str, ...],
) -> None:
    if step.each is not None:
        if step.each not in ancestors:
            raise PratError(f"{label}.each: {step.each!r} is not an ancestor of this step.")
        source = next(item for item in workflow.steps if item.name == step.each)
        if source.each is not None:
            raise PratError(f"{label}.each: step {step.each!r} is itself an each step.")
    if step.as_parameter is not None:
        if step.as_parameter not in parameters:
            raise PratError(
                f"{label}.as: {step.selector!r} has no parameter {step.as_parameter!r}."
            )
        if step.as_parameter in step.bind:
            raise PratError(f"{label}.as: {step.as_parameter!r} is also bound in bind.")


def _validate_workflow_step(
    config: Config, workflow: Workflow, step: WorkflowStep, label: str
) -> None:
    if step.selector in config.workflows:
        raise PratError(
            f"{label}.profile: {step.selector!r} is a workflow, not a profile or agent."
        )
    if step.selector not in config.profiles and step.selector not in BY_SELECTOR:
        raise PratError(
            f"{label}.profile: unknown selector {step.selector!r}; "
            "run 'prat agents' or 'prat profiles' to list choices."
        )
    prompt = step_prompt(config, step)
    parameters = () if prompt is None else template_parameters(prompt)
    ancestors = _ancestors(workflow, step)
    for parameter, source in step.bind.items():
        if source not in ancestors:
            raise PratError(
                f"{label}.bind.{parameter}: {source!r} is not an ancestor of this step."
            )
        if parameter not in parameters:
            raise PratError(
                f"{label}.bind.{parameter}: {step.selector!r} has no parameter {parameter!r}."
            )
    _validate_each(workflow, step, label, ancestors, parameters)
    if prompt is None and step.input == "none" and not step.bind:
        raise PratError(
            f'{label}: {step.selector!r} has no prompt and input is "none", '
            "so the step can never receive input."
        )


def _validate_workflows(config: Config) -> None:
    for name, workflow in config.workflows.items():
        if name in config.profiles:
            raise PratError(
                f"{workflow.source}: workflows.{name}: name is also a profile; "
                "choose a different workflow name."
            )
    for name, workflow in config.workflows.items():
        for step in workflow.steps:
            label = f"{workflow.source}: workflow {name!r} step {step.name!r}"
            _validate_workflow_step(config, workflow, step, label)


def _read_config(path: Path, *, required: bool) -> bytes | None:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        if not required:
            return None
        raise PratError(f"{path}: config file does not exist.") from error
    except OSError as error:
        raise PratError(f"{path}: cannot read config: {error}.") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PratError(f"{path}: cannot read config: not a regular file.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            contents = stream.read(CONFIG_BYTES + 1)
    except OSError as error:
        raise PratError(f"{path}: cannot read config: {error}.") from error
    finally:
        os.close(descriptor)
    if len(contents) > CONFIG_BYTES:
        raise PratError(f"{path}: config exceeds the {CONFIG_BYTES} byte limit.")
    return contents


def _load_file(path: Path, *, required: bool) -> Config:
    contents = _read_config(path, required=required)
    return Config(path=path) if contents is None else _parse_file(path, contents)


def _parse_file(path: Path, raw: bytes) -> Config:
    try:
        contents = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as error:
        raise PratError(f"{path}: cannot read config: {error}.") from error
    try:
        table = tomllib.loads(contents)
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise PratError(f"{path}: invalid TOML: {error}.") from error
    except RecursionError as error:
        raise PratError(f"{path}: invalid TOML: nesting is too deep.") from error
    _known_keys(
        table,
        frozenset({"version", "defaults", "agents", "profiles", "templates", "workflows"}),
        str(path),
    )
    if type(table.get("version")) is not int or table["version"] != 1:
        raise PratError(f"{path}: version must be the integer 1.")
    defaults = parse_options(
        _table(table.get("defaults", {}), f"{path}: defaults"),
        f"{path}: defaults",
        base=path.parent,
    )
    commands, prefixes = _agent_settings(_table(table.get("agents", {}), f"{path}: agents"), path)
    layers = _profiles(_table(table.get("profiles", {}), f"{path}: profiles"), path)
    templates = _templates(_table(table.get("templates", {}), f"{path}: templates"), path)
    workflows = _workflows(_table(table.get("workflows", {}), f"{path}: workflows"), path)
    return Config(
        path=path,
        exists=True,
        defaults=defaults,
        commands=MappingProxyType(commands),
        command_prefixes=MappingProxyType(prefixes),
        profile_layers=MappingProxyType(layers),
        templates=MappingProxyType(templates),
        workflows=MappingProxyType(workflows),
        sources=(path,),
        default_sources=MappingProxyType(
            {name: path for name, value in asdict(defaults).items() if value is not None}
        ),
    )


def _merge_configs(global_config: Config, local_config: Config) -> Config:
    if not local_config.exists:
        return global_config
    collisions = sorted(global_config.profile_layers.keys() & local_config.profile_layers.keys())
    warnings = tuple(
        f"{local_config.path}: profile {name!r} replaces the profile from {global_config.path}."
        for name in collisions
    )
    template_collisions = sorted(global_config.templates.keys() & local_config.templates.keys())
    warnings += tuple(
        f"{local_config.path}: template {name!r} replaces the template from {global_config.path}."
        for name in template_collisions
    )
    workflow_collisions = sorted(global_config.workflows.keys() & local_config.workflows.keys())
    warnings += tuple(
        f"{local_config.path}: workflow {name!r} replaces the workflow from {global_config.path}."
        for name in workflow_collisions
    )
    default_sources = _without_instruction_peer(
        global_config.default_sources, local_config.defaults
    ) | dict(local_config.default_sources)
    return Config(
        path=local_config.path,
        exists=True,
        defaults=merge_options(global_config.defaults, local_config.defaults),
        commands=MappingProxyType(dict(global_config.commands) | dict(local_config.commands)),
        command_prefixes=MappingProxyType(
            dict(global_config.command_prefixes) | dict(local_config.command_prefixes)
        ),
        profile_layers=MappingProxyType(
            dict(global_config.profile_layers) | dict(local_config.profile_layers)
        ),
        templates=MappingProxyType(dict(global_config.templates) | dict(local_config.templates)),
        workflows=MappingProxyType(dict(global_config.workflows) | dict(local_config.workflows)),
        sources=global_config.sources + local_config.sources,
        warnings=warnings,
        default_sources=MappingProxyType(default_sources),
    )


def _same_file(first: Path, second: Path) -> bool:
    if first == second:
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def load_config(
    explicit: str | Path | None = None,
    *,
    cwd: Path,
    env: Mapping[str, str],
    skip_untrusted: bool = False,
) -> Config:
    global_path = config_path(cwd=cwd, env=env)
    local_path = config_path(explicit if explicit is not None else ".pratfile", cwd=cwd, env=env)
    if _same_file(global_path, local_path):
        config = _load_file(local_path, required=explicit is not None)
    else:
        contents = _read_config(local_path, required=explicit is not None)
        if contents is not None and explicit is None:
            state = trust_state(local_path, contents, env)
            if state != "trusted" and not skip_untrusted:
                raise PratError(_untrusted_message(local_path, state))
            contents = contents if state == "trusted" else None
        local = Config(path=local_path) if contents is None else _parse_file(local_path, contents)
        config = replace(
            _merge_configs(_load_file(global_path, required=False), local),
            local_digest=None if contents is None else digest(contents),
        )
    config = replace(config, profiles=MappingProxyType(_resolve_layers(config.profile_layers)))
    for name, profile in config.profiles.items():
        validate_capabilities(
            BY_NAME[profile.agent],
            merge_options(config.defaults, profile.options),
            f"{profile.source}: profiles.{name}",
            origins=option_origins(config, name),
        )
    _validate_workflows(config)
    return config


def _untrusted_message(path: Path, state: TrustState) -> str:
    reason = "changed since it was trusted" if state == "changed" else "is not trusted"
    return f"{path}: local config {reason}; review it, then run 'prat config trust'."


def trust_local_config(
    explicit: str | Path | None = None, *, cwd: Path, env: Mapping[str, str]
) -> tuple[Path, str]:
    path = config_path(explicit if explicit is not None else ".pratfile", cwd=cwd, env=env)
    contents = _read_config(path, required=True) or b""
    _parse_file(path, contents)
    trust(path, contents, env)
    return path, digest(contents)


def resolve_profile(
    config: Config,
    selector: str,
    overrides: Options | None = None,
) -> ResolvedProfile:
    if selector in config.profiles:
        profile = config.profiles[selector]
        agent = BY_NAME[profile.agent]
        profile_name: str | None = selector
        profile_options = profile.options
    elif selector in BY_SELECTOR:
        agent = BY_SELECTOR[selector]
        profile_name = None
        profile_options = Options()
    else:
        raise PratError(
            f"Unknown selector {selector!r}; run 'prat agents' or 'prat profiles' to list choices.",
            code="invalid_arguments",
        )
    options = merge_options(
        Options(timeout=DEFAULT_TIMEOUT, native_args=()),
        config.defaults,
        Options(command_prefix=config.command_prefixes.get(agent.name)),
        profile_options,
        overrides if overrides is not None else Options(),
    )
    validate_capabilities(
        agent,
        options,
        f"{config.path}: selector {selector!r}",
        origins=option_origins(config, selector, overrides),
    )
    return ResolvedProfile(
        agent,
        profile_name,
        (*(options.command_prefix or ()), *config.commands.get(agent.name, agent.command)),
        options,
    )


def agent_command(config: Config, agent: AgentSpec) -> tuple[str, ...]:
    prefix = config.command_prefixes.get(agent.name, config.defaults.command_prefix)
    return (*(prefix or ()), *config.commands.get(agent.name, agent.command))


def init_config(explicit: str | Path | None = None, *, cwd: Path, env: Mapping[str, str]) -> Path:
    path = config_path(explicit, cwd=cwd, env=env)
    try:
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
    except OSError as error:
        raise PratError(f"{path}: cannot create config directory: {error}.") from error
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(SAMPLE_CONFIG)
    except FileExistsError as error:
        raise PratError(
            f"{path}: already exists; edit it or choose another --config path."
        ) from error
    except OSError as error:
        raise PratError(f"{path}: cannot create config: {error}.") from error
    return path
