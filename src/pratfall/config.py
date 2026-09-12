import math
import os
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType

from pratfall.catalog import BY_NAME, BY_SELECTOR, RESERVED_NAMES, get_agent
from pratfall.errors import PratError
from pratfall.models import AgentSpec, Config, Options, Profile, ResolvedProfile

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
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PratError(f"{label}: expected a positive integer.")
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


def parse_options(table: dict[str, object], label: str) -> Options:
    _known_keys(table, OPTION_FIELDS, label)
    return Options(
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
        native_args=(
            _arguments(table["native_args"], f"{label}.native_args")
            if "native_args" in table
            else None
        ),
    )


def merge_options(*layers: Options) -> Options:
    values: dict[str, object] = {}
    for layer in layers:
        values.update({key: value for key, value in asdict(layer).items() if value is not None})
    return parse_options(values, "options")


def validate_capabilities(agent: AgentSpec, options: Options, label: str) -> None:
    caps = agent.capabilities
    if options.model is not None and not caps.model:
        raise PratError(f"{label}.model: {agent.label} does not support a model override.")
    if options.effort is not None:
        if not caps.effort:
            raise PratError(f"{label}.effort: {agent.label} does not support an effort override.")
        if caps.effort_values and options.effort not in caps.effort_values:
            allowed = ", ".join(caps.effort_values)
            raise PratError(f"{label}.effort: {agent.label} accepts: {allowed}.")
    for field in ("max_budget_usd", "max_turns", "max_ai_credits"):
        if getattr(options, field) is not None and field not in caps.budgets:
            raise PratError(f"{label}.{field}: {agent.label} does not support this budget.")
    if options.fast is not None and not caps.fast:
        raise PratError(f"{label}.fast: {agent.label} does not support a fast-mode override.")


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


def _profiles(table: dict[str, object], path: Path, defaults: Options) -> dict[str, Profile]:
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
            {key: val for key, val in settings.items() if key != "agent"}, label
        )
        validate_capabilities(agent, merge_options(defaults, options), label)
        profiles[name] = Profile(agent=agent.name, options=options)
    return profiles


def load_config(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Config:
    path = config_path(explicit, cwd=cwd)
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        if explicit is None:
            return Config(path=path)
        raise PratError(f"{path}: config file does not exist.") from error
    except (OSError, UnicodeError) as error:
        raise PratError(f"{path}: cannot read config: {error}.") from error
    try:
        table = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as error:
        raise PratError(f"{path}: invalid TOML: {error}.") from error
    _known_keys(table, frozenset({"version", "defaults", "agents", "profiles"}), str(path))
    if type(table.get("version")) is not int or table["version"] != 1:
        raise PratError(f"{path}: version must be the integer 1.")
    defaults = parse_options(
        _table(table.get("defaults", {}), f"{path}: defaults"), f"{path}: defaults"
    )
    commands = _commands(_table(table.get("agents", {}), f"{path}: agents"), path)
    profiles = _profiles(_table(table.get("profiles", {}), f"{path}: profiles"), path, defaults)
    return Config(path, True, defaults, MappingProxyType(commands), MappingProxyType(profiles))


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
    validate_capabilities(agent, options, f"{config.path}: selector {selector!r}")
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
