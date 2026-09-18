import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType

from pratfall.catalog import BY_NAME, BY_SELECTOR, RESERVED_NAMES, get_agent
from pratfall.errors import PratError
from pratfall.instructions import validate_instructions
from pratfall.models import (
    MAX_TURNS,
    AgentSpec,
    Config,
    OptionOrigin,
    Options,
    Profile,
    PromptTemplate,
    ResolvedProfile,
)
from pratfall.option_paths import resolve_path
from pratfall.prompt_templates import validate_template

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


def config_path(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Path:
    base = Path.cwd() if cwd is None else cwd
    try:
        if explicit is not None:
            path = Path(explicit).expanduser()
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
            path = root / "pratfall" / "config.toml"
    except RuntimeError as error:
        requested = explicit if explicit is not None else os.environ.get("XDG_CONFIG_HOME", "")
        raise PratError(
            f"{requested or 'config path'}: cannot expand user path ({error}); "
            "use an absolute path or a valid ~user path."
        ) from error
    return Path(os.path.abspath(base / path))


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


def _arguments(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise PratError(f"{label}: expected an array of strings.")
    if any(not isinstance(item, str) or "\0" in item for item in value):
        raise PratError(f"{label}: expected an array of strings without NUL bytes.")
    return tuple(value)


def _directories(value: object, label: str, base: Path | None) -> tuple[str, ...]:
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
    return Options(
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
        add_dirs=_directories(table["add_dirs"], f"{label}.add_dirs", base)
        if "add_dirs" in table
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
        native_args=(
            _arguments(table["native_args"], f"{label}.native_args")
            if "native_args" in table
            else None
        ),
    )


def merge_options(*layers: Options) -> Options:
    values: dict[str, object] = {}
    for layer in layers:
        _clear_instruction_peer(values, layer)
        values.update({key: value for key, value in asdict(layer).items() if value is not None})
    return parse_options(values, "options")


def _clear_instruction_peer[T](values: dict[str, T], layer: Options) -> None:
    if layer.instructions is not None:
        values.pop("instructions_file", None)
    if layer.instructions_file is not None:
        values.pop("instructions", None)


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
    if options.model is not None and not caps.model:
        raise _rejected(fields["model"], f"{agent.label} does not support a model override.")
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
    if options.fast is not None and not caps.fast:
        raise _rejected(fields["fast"], f"{agent.label} does not support a fast-mode override.")


def option_origins(
    config: Config, selector: str, overrides: Options | None = None
) -> dict[str, OptionOrigin]:
    origins = {
        name: OptionOrigin(f"{source}: defaults.{name} (selector {selector!r})")
        for name, source in config.default_sources.items()
    }
    profile = config.profiles.get(selector)
    if profile is not None:
        _clear_instruction_peer(origins, profile.options)
        origins.update(
            {
                name: OptionOrigin(f"{profile.source or config.path}: profiles.{selector}.{name}")
                for name, value in asdict(profile.options).items()
                if value is not None
            }
        )
    if overrides is not None:
        _clear_instruction_peer(origins, overrides)
        origins.update(
            {
                name: OptionOrigin(
                    f"command line: selector {selector!r}.{name}", "invalid_arguments"
                )
                for name, value in asdict(overrides).items()
                if value is not None
            }
        )
    return origins


def _commands(table: dict[str, object], path: Path) -> dict[str, tuple[str, ...]]:
    commands: dict[str, tuple[str, ...]] = {}
    for name, value in table.items():
        label = f"{path}: agents.{name}"
        if name not in BY_NAME:
            raise PratError(f"{label}: use a canonical name from 'prat agents'.")
        settings = _table(value, label)
        _known_keys(settings, frozenset({"command"}), label)
        if "command" not in settings:
            continue
        command = _arguments(settings["command"], f"{label}.command")
        if not command or not command[0].strip():
            raise PratError(f"{label}.command: expected a nonempty executable argv prefix.")
        executable = command[0]
        if "/" in executable and not Path(executable).is_absolute():
            executable = str(Path(os.path.abspath(path.parent / executable)))
        commands[name] = (executable, *command[1:])
    return commands


def _profiles(table: dict[str, object], path: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for name, value in table.items():
        label = f"{path}: profiles.{name}"
        if name in RESERVED_NAMES:
            raise PratError(f"{label}: name is reserved; choose a different profile name.")
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name) is None:
            raise PratError(
                f"{label}: use letters, digits, underscores or hyphens in profile names."
            )
        settings = _table(value, label)
        _known_keys(settings, OPTION_FIELDS | {"agent"}, label)
        agent_name = _string(settings.get("agent"), f"{label}.agent")
        try:
            agent = get_agent(agent_name)
        except PratError as error:
            raise PratError(f"{label}.agent: {error}") from error
        options = parse_options(
            {key: val for key, val in settings.items() if key != "agent"}, label, base=path.parent
        )
        profiles[name] = Profile(agent=agent.name, options=options, source=path)
    return profiles


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


def _load_file(path: Path, *, required: bool) -> Config:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        if not required:
            return Config(path=path)
        raise PratError(f"{path}: config file does not exist.") from error
    except (OSError, UnicodeError) as error:
        raise PratError(f"{path}: cannot read config: {error}.") from error
    try:
        table = tomllib.loads(contents)
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise PratError(f"{path}: invalid TOML: {error}.") from error
    _known_keys(
        table, frozenset({"version", "defaults", "agents", "profiles", "templates"}), str(path)
    )
    if type(table.get("version")) is not int or table["version"] != 1:
        raise PratError(f"{path}: version must be the integer 1.")
    defaults = parse_options(
        _table(table.get("defaults", {}), f"{path}: defaults"),
        f"{path}: defaults",
        base=path.parent,
    )
    commands = _commands(_table(table.get("agents", {}), f"{path}: agents"), path)
    profiles = _profiles(_table(table.get("profiles", {}), f"{path}: profiles"), path)
    templates = _templates(_table(table.get("templates", {}), f"{path}: templates"), path)
    return Config(
        path=path,
        exists=True,
        defaults=defaults,
        commands=MappingProxyType(commands),
        profiles=MappingProxyType(profiles),
        templates=MappingProxyType(templates),
        sources=(path,),
        default_sources=MappingProxyType(
            {name: path for name, value in asdict(defaults).items() if value is not None}
        ),
    )


def _merge_configs(global_config: Config, local_config: Config) -> Config:
    if not local_config.exists:
        return global_config
    collisions = sorted(global_config.profiles.keys() & local_config.profiles.keys())
    warnings = tuple(
        f"{local_config.path}: profile {name!r} replaces the profile from {global_config.path}."
        for name in collisions
    )
    template_collisions = sorted(global_config.templates.keys() & local_config.templates.keys())
    warnings += tuple(
        f"{local_config.path}: template {name!r} replaces the template from {global_config.path}."
        for name in template_collisions
    )
    default_sources = dict(global_config.default_sources)
    _clear_instruction_peer(default_sources, local_config.defaults)
    default_sources.update(local_config.default_sources)
    return Config(
        path=local_config.path,
        exists=True,
        defaults=merge_options(global_config.defaults, local_config.defaults),
        commands=MappingProxyType(dict(global_config.commands) | dict(local_config.commands)),
        profiles=MappingProxyType(dict(global_config.profiles) | dict(local_config.profiles)),
        templates=MappingProxyType(dict(global_config.templates) | dict(local_config.templates)),
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


def load_config(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Config:
    global_path = config_path(cwd=cwd)
    local_path = config_path(explicit if explicit is not None else ".pratfile", cwd=cwd)
    if _same_file(global_path, local_path):
        config = _load_file(local_path, required=explicit is not None)
    else:
        config = _merge_configs(
            _load_file(global_path, required=False),
            _load_file(local_path, required=explicit is not None),
        )
    for name, profile in config.profiles.items():
        validate_capabilities(
            BY_NAME[profile.agent],
            merge_options(config.defaults, profile.options),
            f"{profile.source}: profiles.{name}",
            origins=option_origins(config, name),
        )
    return config


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
        config.commands.get(agent.name, agent.command),
        options,
    )


def init_config(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Path:
    path = config_path(explicit, cwd=cwd)
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
