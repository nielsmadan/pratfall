import json

import pytest

from adapter_helpers import resolved
from pratfall.adapters import cursor
from pratfall.models import DecodedOutput, Options


@pytest.mark.parametrize("prompt", [b"login", b"--force"])
def test_cursor_builds_explicit_agent_command_and_protected_prompt(prompt: bytes) -> None:
    invocation = cursor.build(
        resolved(
            "cursor",
            Options(model="cursor-model", native_args=("--mode", "ask")),
        ),
        prompt,
    )
    assert invocation.argv == (
        "cursor-wrapper",
        "native",
        "--print",
        "--output-format",
        "json",
        "--model",
        "cursor-model",
        "--mode",
        "ask",
        "agent",
        "--",
        prompt.decode(),
    )
    assert invocation.stdin == b""


def test_cursor_decodes_documented_single_json_success() -> None:
    decoded = cursor.decode(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "final answer",
                "duration_ms": 1234,
                "session_id": "id",
                "future": True,
            }
        )
    )
    assert decoded == DecodedOutput(output="final answer", session_id="id")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        "{}",
        '{"type":"result","subtype":"error","is_error":true,"result":"partial"}',
        '{"type":"result","subtype":"success","is_error":false,"result":3}',
    ],
)
def test_cursor_rejects_malformed_or_unsuccessful_results(payload: str) -> None:
    decoded = cursor.decode(payload)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"
