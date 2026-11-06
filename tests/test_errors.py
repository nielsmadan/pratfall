import pytest

from pratfall.errors import did_you_mean


@pytest.mark.parametrize(
    ("value", "otherwise", "expected"),
    [
        ("clade", "", "; did you mean 'claude'?"),
        ("clade", "run 'prat agents' to list agents", "; did you mean 'claude'?"),
        ("zzzz", "run 'prat agents' to list agents", "; run 'prat agents' to list agents"),
        ("zzzz", "", ""),
    ],
)
def test_did_you_mean_names_the_closest_candidate_or_the_fallback(
    value: str, otherwise: str, expected: str
) -> None:
    assert did_you_mean(value, ["codex", "claude", "claude"], otherwise) == expected
