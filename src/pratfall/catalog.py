from types import MappingProxyType

from pratfall.errors import PratError, did_you_mean
from pratfall.models import AgentSpec, Capabilities

AGENTS = (
    AgentSpec(
        "claude",
        "Claude Code",
        ("cc",),
        ("claude",),
        Capabilities(
            reports_session_id=True,
            schema=True,
            effort=True,
            effort_values=("low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_budget_usd", "max_turns"}),
            fast=True,
            add_dirs=True,
            instructions=True,
            tools=True,
            disabled_tools=True,
            native_agent=True,
            session_id=True,
            tools_empty=True,
            tools_scope="built-ins; MCP unaffected; EndConversation may remain",
            disabled_tools_scope="native deny rules; EndConversation exception",
        ),
    ),
    AgentSpec(
        "codex",
        "Codex",
        ("cx",),
        ("codex",),
        Capabilities(
            reports_session_id=True,
            schema=True,
            effort=True,
            fast=True,
            add_dirs=True,
            instructions=True,
            attachments=True,
            attachment_types=("image",),
        ),
    ),
    AgentSpec("gemini", "Gemini", ("gm",), ("gemini",), Capabilities(add_dirs=True)),
    AgentSpec(
        "antigravity",
        "Antigravity",
        ("ag", "agy"),
        ("agy",),
        Capabilities(effort=True, effort_values=("low", "medium", "high")),
    ),
    AgentSpec(
        "copilot",
        "Copilot",
        ("cp",),
        ("copilot",),
        Capabilities(
            reports_session_id=True,
            session_id=True,
            effort=True,
            effort_values=("low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_ai_credits"}),
            attachments=True,
            attachment_types=("image", "native document"),
            add_dirs=True,
            tools=True,
            disabled_tools=True,
            native_agent=True,
            tools_empty=True,
            tools_scope="model-visible tools",
            disabled_tools_scope="model-visible tools",
        ),
    ),
    AgentSpec(
        "kiro",
        "Kiro",
        (),
        ("kiro-cli",),
        Capabilities(
            effort=True,
            effort_values=("low", "medium", "high", "xhigh", "max"),
        ),
    ),
    AgentSpec(
        "cursor",
        "Cursor",
        ("cu",),
        ("agent",),
        Capabilities(reports_session_id=True),
    ),
    AgentSpec("openclaw", "OpenClaw", ("claw",), ("openclaw",), Capabilities(effort=True)),
    AgentSpec(
        "hermes",
        "Hermes",
        ("hm",),
        ("hermes",),
        Capabilities(
            effort=True,
            effort_values=(
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
                "ultra",
            ),
            budgets=frozenset({"max_turns"}),
            attachments=True,
            attachment_types=("image",),
            attachment_max_count=1,
        ),
    ),
    AgentSpec(
        "opencode",
        "OpenCode",
        ("oc",),
        ("opencode",),
        Capabilities(effort=True, attachments=True, attachment_types=("file",)),
    ),
    AgentSpec("kimi", "Kimi Code", (), ("kimi",), Capabilities(reports_session_id=True)),
    AgentSpec("goose", "Goose", (), ("goose",), Capabilities(budgets=frozenset({"max_turns"}))),
    AgentSpec(
        "kilo",
        "Kilo",
        (),
        ("kilo",),
        Capabilities(effort=True, attachments=True, attachment_types=("file",)),
    ),
    AgentSpec("warp", "Warp", (), ("oz",)),
    AgentSpec(
        "qwen",
        "Qwen Code",
        (),
        ("qwen",),
        Capabilities(
            schema=True,
            budgets=frozenset({"max_turns"}),
            add_dirs=True,
            instructions=True,
            tools=True,
            disabled_tools=True,
            tools_scope="native core allowlist; schema tool exempt; native settings apply",
            disabled_tools_scope="native deny rules, including schema tool",
        ),
    ),
    AgentSpec("amp", "Amp", (), ("amp",), Capabilities(model=False), version_args=("version",)),
    AgentSpec("reasonix", "Reasonix", ("rx",), ("reasonix",), Capabilities(effort=True)),
    AgentSpec(
        "droid",
        "Droid",
        ("dr",),
        ("droid",),
        Capabilities(
            reports_session_id=True,
            instructions=True,
            tools=True,
            disabled_tools=True,
            tools_scope="native run tools",
            disabled_tools_scope="native run tools",
            effort=True,
            effort_values=(
                "none",
                "dynamic",
                "off",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        ),
    ),
    AgentSpec(
        "vibe",
        "Mistral Vibe",
        (),
        ("vibe",),
        Capabilities(
            model=False,
            native_agent=True,
            budgets=frozenset({"max_turns", "max_budget_usd"}),
            tools=True,
            disabled_tools=True,
            tools_scope="native available tools, including MCP and connectors",
            disabled_tools_scope="native available tools, including MCP and connectors",
        ),
    ),
    AgentSpec("crush", "Crush", ("cr",), ("crush",), Capabilities(effort=True)),
    AgentSpec("devin", "Devin", ("dv",), ("devin",)),
    AgentSpec(
        "cortex",
        "Cortex Code",
        ("co",),
        ("cortex",),
        Capabilities(
            effort=True,
            effort_values=("minimal", "low", "medium", "high", "max"),
            budgets=frozenset({"max_turns"}),
        ),
    ),
    AgentSpec(
        "grok",
        "Grok Build",
        (),
        ("grok",),
        Capabilities(
            reports_session_id=True,
            session_id=True,
            effort=True,
            effort_values=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_turns"}),
        ),
    ),
    AgentSpec(
        "pi",
        "Pi",
        (),
        ("pi",),
        Capabilities(
            effort=True,
            effort_values=("off", "minimal", "low", "medium", "high", "xhigh", "max"),
            tools=True,
            disabled_tools=True,
            tools_empty=True,
            tools_scope="built-in, extension and custom tools",
            disabled_tools_scope="built-in, extension and custom tools",
            attachments=True,
            attachment_types=("image", "text file"),
        ),
    ),
)
BY_NAME = MappingProxyType({agent.name: agent for agent in AGENTS})
BY_SELECTOR = MappingProxyType(
    {selector: agent for agent in AGENTS for selector in (agent.name, *agent.aliases)}
)
MANAGEMENT_COMMANDS = frozenset(
    {
        "list",
        "doctor",
        "config",
        "trust",
        "completion",
        "help",
        "version",
    }
)
RESERVED_NAMES = frozenset(BY_SELECTOR) | MANAGEMENT_COMMANDS | {"help", "version"}
REMOVED_AGENTS = MappingProxyType(dict.fromkeys(("openhands", "oh", "iflow", "if"), "0.9.0"))
REMOVED_COMMANDS = MappingProxyType(
    dict.fromkeys(("agents", "profiles", "templates", "workflows"), "1.3.0")
)


def shadowed_by(name: str) -> str | None:
    if name in BY_NAME:
        return f"the built-in agent {name!r}"
    if name in BY_SELECTOR:
        return f"the alias {name!r} of the built-in agent {BY_SELECTOR[name].name!r}"
    if name in RESERVED_NAMES:
        return f"the command {name!r}"
    return None


def removed_agent(name: str) -> str | None:
    version = REMOVED_AGENTS.get(name)
    if version is None:
        return None
    return f"agent {name!r} was removed in {version}; run 'prat list agents' to list choices"


def removed_command(name: str) -> str | None:
    version = REMOVED_COMMANDS.get(name)
    if version is None:
        return None
    return f"'prat {name}' was removed in {version}; run 'prat list {name}'"


def get_agent(selector: str) -> AgentSpec:
    try:
        return BY_SELECTOR[selector]
    except KeyError:
        raise PratError(
            removed_agent(selector)
            or f"unknown agent {selector!r}"
            + did_you_mean(selector, BY_SELECTOR, "run 'prat list agents' to list agents")
        ) from None
