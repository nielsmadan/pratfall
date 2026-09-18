from types import MappingProxyType

from pratfall.errors import PratError
from pratfall.models import AgentSpec, Capabilities

AGENTS = (
    AgentSpec(
        "claude",
        "Claude Code",
        ("cc",),
        ("claude",),
        Capabilities(
            effort=True,
            effort_values=("low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_budget_usd", "max_turns"}),
            fast=True,
            add_dirs=True,
            instructions=True,
            tools=True,
            disabled_tools=True,
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
        Capabilities(effort=True, fast=True, add_dirs=True, instructions=True),
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
            effort=True,
            effort_values=("low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_ai_credits"}),
            add_dirs=True,
            tools=True,
            disabled_tools=True,
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
    AgentSpec("cursor", "Cursor", ("cu",), ("agent",)),
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
        ),
    ),
    AgentSpec("opencode", "OpenCode", ("oc",), ("opencode",), Capabilities(effort=True)),
    AgentSpec("openhands", "OpenHands", ("oh",), ("openhands",), Capabilities(model=False)),
    AgentSpec("warp", "Warp", (), ("oz",)),
    AgentSpec("iflow", "iFlow", ("if",), ("iflow",)),
    AgentSpec(
        "qwen",
        "Qwen Code",
        (),
        ("qwen",),
        Capabilities(
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
    AgentSpec("kimi", "Kimi", (), ("kimi",)),
    AgentSpec(
        "vibe",
        "Mistral Vibe",
        (),
        ("vibe",),
        Capabilities(
            model=False,
            budgets=frozenset({"max_turns", "max_budget_usd"}),
            tools=True,
            disabled_tools=True,
            tools_scope="native available tools, including MCP and connectors",
            disabled_tools_scope="native available tools, including MCP and connectors",
        ),
    ),
    AgentSpec("crush", "Crush", ("cr",), ("crush",)),
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
            effort=True,
            effort_values=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
            budgets=frozenset({"max_turns"}),
        ),
    ),
)
BY_NAME = MappingProxyType({agent.name: agent for agent in AGENTS})
BY_SELECTOR = MappingProxyType(
    {selector: agent for agent in AGENTS for selector in (agent.name, *agent.aliases)}
)
MANAGEMENT_COMMANDS = frozenset({"agents", "profiles", "templates", "doctor", "config"})
RESERVED_NAMES = frozenset(BY_SELECTOR) | MANAGEMENT_COMMANDS | {"help", "version"}


def get_agent(selector: str) -> AgentSpec:
    try:
        return BY_SELECTOR[selector]
    except KeyError:
        raise PratError(f"Unknown agent {selector!r}; run 'prat agents' to list agents.") from None
