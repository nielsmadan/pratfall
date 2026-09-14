import pytest

from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import AGENTS, BY_NAME, BY_SELECTOR, RESERVED_NAMES, get_agent
from pratfall.errors import PratError


@pytest.mark.parametrize(
    ("name", "aliases"),
    [
        ("claude", ("cc",)),
        ("codex", ("cx",)),
        ("gemini", ("gm",)),
        ("antigravity", ("ag", "agy")),
        ("copilot", ("cp",)),
        ("kiro", ()),
        ("cursor", ("cu",)),
        ("openclaw", ("claw",)),
        ("hermes", ("hm",)),
        ("opencode", ("oc",)),
        ("openhands", ("oh",)),
        ("warp", ()),
        ("iflow", ("if",)),
        ("qwen", ()),
        ("amp", ()),
        ("reasonix", ("rx",)),
        ("droid", ("dr",)),
        ("kimi", ()),
        ("vibe", ()),
        ("crush", ("cr",)),
        ("devin", ("dv",)),
        ("cortex", ("co",)),
        ("grok", ()),
    ],
)
def test_agent_names_and_aliases_resolve(name: str, aliases: tuple[str, ...]) -> None:
    agent = get_agent(name)
    assert agent.name == name
    assert agent.aliases == aliases
    for alias in aliases:
        assert get_agent(alias) == agent


def test_registry_has_completed_agents_and_unique_selectors() -> None:
    assert len(AGENTS) == 23
    assert set(ADAPTERS) == set(BY_NAME)
    assert len(BY_SELECTOR) == sum(len(agent.aliases) + 1 for agent in AGENTS)
    assert RESERVED_NAMES.issuperset({"agents", "profiles", "doctor", "config", *BY_SELECTOR})


def test_kiro_supports_current_native_model_and_effort_options() -> None:
    assert BY_NAME["kiro"].capabilities.model is True
    assert BY_NAME["kiro"].capabilities.effort_values == (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


def test_only_verified_agents_support_fast_mode() -> None:
    assert {agent.name for agent in AGENTS if agent.capabilities.fast} == {"claude", "codex"}
    assert {agent.name: agent.version_args for agent in AGENTS} == {
        name: (("version",) if name == "amp" else ("--version",)) for name in BY_NAME
    }
    assert BY_NAME["amp"].aliases == ()


def test_unknown_agent_is_actionable() -> None:
    with pytest.raises(PratError, match="prat agents"):
        get_agent("unknown")
