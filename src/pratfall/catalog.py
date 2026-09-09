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
        ),
    ),
    AgentSpec("codex", "Codex", ("cx",), ("codex",), Capabilities(effort=True)),
    AgentSpec("gemini", "Gemini", ("gm",), ("gemini",)),
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
        ),
    ),
    AgentSpec(
        "kiro",
        "Kiro",
        ("ki",),
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
)
BY_NAME = MappingProxyType({agent.name: agent for agent in AGENTS})
BY_SELECTOR = MappingProxyType(
    {selector: agent for agent in AGENTS for selector in (agent.name, *agent.aliases)}
)
MANAGEMENT_COMMANDS = frozenset({"agents", "profiles", "doctor", "config"})
RESERVED_NAMES = frozenset(BY_SELECTOR) | MANAGEMENT_COMMANDS | {"help", "version"}


def get_agent(selector: str) -> AgentSpec:
    try:
        return BY_SELECTOR[selector]
    except KeyError:
        raise PratError(f"Unknown agent {selector!r}; run 'prat agents' to list agents.") from None
