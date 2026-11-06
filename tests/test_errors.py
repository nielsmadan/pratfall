import ast
from pathlib import Path

import pytest

from pratfall.catalog import AGENTS
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


_SOURCE = Path(__file__).resolve().parents[1] / "src" / "pratfall"
_MESSAGE_CALLS = frozenset(
    {"PratError", "ResultError", "malformed", "_protocol", "_argument_error"}
)
_PROPER_NOUNS = frozenset(
    {word for agent in AGENTS for word in agent.label.split()} | {"Claude", "Qwen", "Grok", "Vibe"}
)


def _message_literals() -> list[tuple[str, int, str]]:
    found = []
    for path in sorted(_SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for argument in node.args if name in _MESSAGE_CALLS else ():
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found.append((path.name, node.lineno, argument.value))
                elif isinstance(argument, ast.JoinedStr):
                    text = "".join(
                        str(part.value) if isinstance(part, ast.Constant) else "{}"
                        for part in argument.values
                    )
                    found.append((path.name, node.lineno, text))
    return found


def _breaks_style(message: str) -> bool:
    first = message.split(" ", 1)[0]
    capitalized = first[:1].isupper() and first not in _PROPER_NOUNS and not first.isupper()
    return capitalized or message.endswith(".")


def test_message_literals_start_lowercase_without_a_trailing_period() -> None:
    literals = _message_literals()
    assert len(literals) > 300
    assert [item for item in literals if _breaks_style(item[2])] == []
