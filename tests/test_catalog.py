import pytest

from pratfall.catalog import AGENTS, BY_NAME, BY_SELECTOR, RESERVED_NAMES, get_agent
from pratfall.errors import PratError


@pytest.mark.parametrize(
    ("selector", "canonical"),
    [
        ("cc", "claude"),
        ("cx", "codex"),
        ("gm", "gemini"),
        ("ag", "antigravity"),
        ("agy", "antigravity"),
        ("cp", "copilot"),
        ("ki", "kiro"),
        ("cu", "cursor"),
        ("claw", "openclaw"),
        ("hm", "hermes"),
        ("oc", "opencode"),
    ],
)
def test_aliases_resolve_to_canonical_agent(selector: str, canonical: str) -> None:
    assert get_agent(selector) == BY_NAME[canonical]
    assert get_agent(canonical).name == canonical


def test_registry_has_ten_agents_and_unique_selectors() -> None:
    assert len(AGENTS) == 10
    assert len(BY_SELECTOR) == sum(len(agent.aliases) + 1 for agent in AGENTS)
    assert RESERVED_NAMES.issuperset({"agents", "profiles", "doctor", "config", *BY_SELECTOR})


def test_unknown_agent_is_actionable() -> None:
    with pytest.raises(PratError, match="prat agents"):
        get_agent("unknown")
