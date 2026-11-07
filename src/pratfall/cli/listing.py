import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path

from pratfall.catalog import AGENTS
from pratfall.cli.doctor import Which, agent_executable
from pratfall.cli.parsing import LIST_SECTIONS
from pratfall.cli.presentation import _limit_text, render_table
from pratfall.config import option_origins, resolve_profile
from pratfall.limits import DEFAULT_MAX_PARALLEL
from pratfall.models import AgentSpec, Config, OptionOrigin, ProfileLayer, WorkflowStep
from pratfall.prompt_templates import template_parameters
from pratfall.workflow_plan import workflow_parameter_names

View = tuple[dict[str, object], list[str]]
_Section = tuple[list[dict[str, object]], list[str]]

_JSON_RENDERED_OPTIONS = frozenset(
    {
        "native_args",
        "command_prefix",
        "add_dirs",
        "attachments",
        "instructions",
        "schema",
        "instructions_file",
        "tools",
        "disabled_tools",
        "native_agent",
        "session_id",
    }
)
_TEXT_OPTIONS = frozenset({"instructions", "native_args"})
_SUPPORTED_FIELDS = (
    "model",
    "effort",
    "fast",
    "add_dirs",
    "attachments",
    "instructions",
    "schema",
    "tools",
    "disabled_tools",
    "native_agent",
    "session_id",
)


def list_view(config: Config, section: str | None, *, width: int | None, which: Which) -> View:
    builders: dict[str, Callable[[], _Section]] = {
        "profiles": lambda: _profiles_section(config, width),
        "workflows": lambda: _workflows_section(config, width),
        "templates": lambda: _templates_section(config),
        "agents": lambda: _agents_section(config, which),
    }
    chosen = tuple(LIST_SECTIONS) if section is None else (section,)
    built = {name: builders[name]() for name in chosen}
    payload: dict[str, object] = {name: records for name, (records, _) in built.items()}
    if section is not None:
        return payload, built[section][1] or [f"No {section} configured."]
    lines: list[str] = []
    for name, (_, table) in built.items():
        if table:
            lines.extend([*([""] if lines else []), name.capitalize(), *table])
    return payload, lines


def _table(
    header: Sequence[str], rows: Sequence[Sequence[str]], *, cut: int, width: int | None
) -> list[str]:
    return render_table([header, *rows], cut=cut, width=width) if rows else []


def _profiles_section(config: Config, width: int | None) -> _Section:
    names = sorted(config.profiles)
    rows = [
        (
            name,
            config.profiles[name].agent,
            config.profile_layers[name].extends or "",
            _sets_text(config.profile_layers[name]),
            _from_text(config, f"profiles.{name}", config.profile_layers[name].source),
        )
        for name in names
    ]
    records = [_profile_record(config, name) for name in names]
    return records, _table(("NAME", "AGENT", "EXTENDS", "SETS", "FROM"), rows, cut=3, width=width)


def _profile_record(config: Config, name: str) -> dict[str, object]:
    resolved = resolve_profile(config, name)
    layer = config.profile_layers[name]
    template = config.profiles[name].prompt
    return {
        "name": name,
        "agent": resolved.agent.name,
        "extends": layer.extends,
        "source": str(layer.source),
        "options": asdict(resolved.options),
        "prompt": None if template is None else template.prompt,
        "prompt_source": None if template is None else str(template.source),
        "parameters": [] if template is None else list(template_parameters(template.prompt)),
    }


def _sets_text(layer: ProfileLayer) -> str:
    items = [
        key
        if key in _TEXT_OPTIONS or isinstance(value, tuple)
        else f"{key}={_setting_text(key, value)}"
        for key, value in asdict(layer.options).items()
        if value is not None
    ]
    if layer.prompt is not None:
        parameters = template_parameters(layer.prompt)
        items.append(f"prompt ({', '.join(parameters)})" if parameters else "prompt")
    return " ".join(items)


def _setting_text(key: str, value: object) -> str:
    if key in _JSON_RENDERED_OPTIONS:
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_local(config: Config, source: Path | None) -> bool:
    return config.local_source is not None and source == config.local_source


def _from_text(config: Config, key: str, source: Path) -> str:
    if not _is_local(config, source):
        return "global"
    return "local, replaces global" if key in config.replaced else "local"


def _from_with_paths(config: Config, key: str, source: Path) -> str:
    replaced = config.replaced.get(key) if _is_local(config, source) else None
    paths = (source,) if replaced is None else (source, replaced)
    return f"{_from_text(config, key, source)} ({', '.join(str(path) for path in paths)})"


def _workflows_section(config: Config, width: int | None) -> _Section:
    names = sorted(config.workflows)
    rows = [
        (
            name,
            ", ".join(step.name for step in config.workflows[name].steps),
            ", ".join(workflow_parameter_names(config, config.workflows[name])),
            _from_text(config, f"workflows.{name}", config.workflows[name].source),
        )
        for name in names
    ]
    records = [_workflow_record(config, name) for name in names]
    return records, _table(("NAME", "STEPS", "PARAMETERS", "FROM"), rows, cut=1, width=width)


def _workflow_record(config: Config, name: str) -> dict[str, object]:
    workflow = config.workflows[name]
    return {
        "name": name,
        "parallel": workflow.parallel,
        "parameters": list(workflow_parameter_names(config, workflow)),
        "source": str(workflow.source),
        "steps": [
            {
                "name": step.name,
                "profile": step.selector,
                "after": list(step.after),
                "input": step.input,
                "bind": dict(step.bind),
                "each": step.each,
                "split": None if step.each is None else step.split,
                "as": step.as_parameter,
                "max_parallel": None if step.each is None else step.max_parallel,
                "max_items": None if step.each is None else step.max_items,
                "overflow": None if step.each is None else step.overflow,
            }
            for step in workflow.steps
        ],
    }


def _templates_section(config: Config) -> _Section:
    templates = sorted(config.templates.items())
    records: list[dict[str, object]] = [
        {
            "name": name,
            "prompt": template.prompt,
            "source": str(template.source),
            "parameters": list(template_parameters(template.prompt)),
        }
        for name, template in templates
    ]
    rows = [
        (
            name,
            ", ".join(template_parameters(template.prompt)),
            _from_text(config, f"templates.{name}", template.source),
        )
        for name, template in templates
    ]
    return records, _table(("NAME", "PARAMETERS", "FROM"), rows, cut=1, width=None)


def _agents_section(config: Config, which: Which) -> _Section:
    installed = {agent.name: which(agent_executable(config, agent)) is not None for agent in AGENTS}
    records = [{**_agent_record(agent), "installed": installed[agent.name]} for agent in AGENTS]
    rows = [(agent.name, ", ".join(agent.aliases)) for agent in AGENTS if installed[agent.name]]
    missing = [agent.name for agent in AGENTS if not installed[agent.name]]
    lines = _table(("NAME", "ALIASES"), rows, cut=1, width=None)
    return records, lines + ([f"not installed: {', '.join(missing)}"] if missing else [])


def _agent_record(agent: AgentSpec) -> dict[str, object]:
    caps = agent.capabilities
    return {
        "name": agent.name,
        "label": agent.label,
        "aliases": list(agent.aliases),
        "command": list(agent.command),
        "capabilities": {
            "model": caps.model,
            "effort": caps.effort,
            "effort_values": list(caps.effort_values) or None,
            "budgets": sorted(caps.budgets),
            "fast": caps.fast,
            "add_dirs": caps.add_dirs,
            "attachments": caps.attachments,
            "attachment_types": list(caps.attachment_types) or None,
            "attachment_max_count": caps.attachment_max_count,
            "instructions": caps.instructions,
            "schema": caps.schema,
            "native_agent": caps.native_agent,
            "session_id": caps.session_id,
            "reports_session_id": caps.reports_session_id,
            "tools": caps.tools,
            "disabled_tools": caps.disabled_tools,
            "tools_empty": caps.tools_empty,
            "tools_scope": caps.tools_scope,
            "disabled_tools_scope": caps.disabled_tools_scope,
        },
    }


def agent_line(agent: AgentSpec) -> str:
    caps = agent.capabilities
    supported = [field for field in _SUPPORTED_FIELDS if getattr(caps, field)]
    supported.extend(sorted(caps.budgets))
    if caps.attachments:
        supported.append("attachment_types=" + "/".join(caps.attachment_types))
        if caps.attachment_max_count is not None:
            supported.append(f"attachment_max_count={caps.attachment_max_count}")
    selectors = f"{agent.name} ({', '.join(agent.aliases)})" if agent.aliases else agent.name
    return f"{selectors}: {', '.join(supported) or 'none'}"


def profile_page(config: Config, name: str) -> str:
    profile = config.profiles[name]
    layer = config.profile_layers[name]
    lines = [
        f"{name}: profile for {profile.agent}, "
        + _from_with_paths(config, f"profiles.{name}", layer.source)
    ]
    if layer.extends is not None:
        lines.append(f"extends: {layer.extends}")
    parameters = () if profile.prompt is None else template_parameters(profile.prompt.prompt)
    if parameters:
        lines.append(f"parameters: {', '.join(parameters)}")
    rows = [("SETTING", "VALUE", "ORIGIN"), *_setting_rows(config, name)]
    return "\n".join([*lines, "", *render_table(rows)])


def _setting_rows(config: Config, name: str) -> list[tuple[str, str, str]]:
    profile = config.profiles[name]
    origins = option_origins(config, name)
    rows = [
        (key, _setting_text(key, value), _origin_text(config, profile.agent, key, origins.get(key)))
        for key, value in asdict(resolve_profile(config, name).options).items()
        if value is not None and (value != () or key == "tools")
    ]
    if profile.prompt is not None:
        declaring = next(layer for layer in reversed(profile.layers) if layer.prompt is not None)
        rows.append(
            (
                "prompt",
                json.dumps(profile.prompt.prompt, ensure_ascii=False),
                _located(config, f"profiles.{declaring.name}", declaring.source),
            )
        )
    return rows


def _origin_text(config: Config, agent: str, key: str, origin: OptionOrigin | None) -> str:
    table = None if origin is None else origin.table
    if table is not None and origin is not None and table.startswith("profiles."):
        return _located(config, table, origin.source)
    if key == "command_prefix" and agent in config.command_prefixes:
        return f"agents.{agent}"
    if table is not None and origin is not None:
        return _located(config, table, origin.source)
    return "built-in default"


def _located(config: Config, table: str, source: Path | None) -> str:
    return f"{table} (local)" if _is_local(config, source) else table


def workflow_page(config: Config, name: str) -> str:
    workflow = config.workflows[name]
    parameters = workflow_parameter_names(config, workflow)
    lines = [f"{name}: workflow, " + _from_with_paths(config, f"workflows.{name}", workflow.source)]
    if parameters:
        lines.append(f"parameters: {', '.join(parameters)}")
    if not workflow.parallel:
        lines.append("parallel: false")
    rows = [
        (step.name, step.selector, ", ".join(step.after), " ".join(_step_options(step)))
        for step in workflow.steps
    ]
    return "\n".join([*lines, "", *render_table([("STEP", "PROFILE", "AFTER", "OPTIONS"), *rows])])


def _step_options(step: WorkflowStep) -> list[str]:
    options = ["input=none"] if step.input == "none" else []
    if step.bind:
        options.append("bind=" + ",".join(f"{key}:{source}" for key, source in step.bind.items()))
    if step.each is None:
        return options
    options.append(f"each={step.each}")
    if step.split != "lines":
        options.append(f"split={step.split}")
    if step.as_parameter is not None:
        options.append(f"as={step.as_parameter}")
    if step.max_parallel != DEFAULT_MAX_PARALLEL:
        options.append(f"max_parallel={step.max_parallel}")
    return options + _limit_text(step).split()
