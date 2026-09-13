import json

import pytest

from adapter_helpers import antigravity_usage, codex_stream, resolved
from pratfall.adapters import antigravity
from pratfall.models import Options, ResultError, Usage


def test_antigravity_builds_one_turn_stream_json_stdin_invocation() -> None:
    invocation = antigravity.build(
        resolved(
            "antigravity",
            Options(model="ag-model", effort="high", native_args=("--sandbox",)),
        ),
        b"multi\nline",
    )
    assert invocation.argv == (
        "antigravity-wrapper",
        "native",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        "ag-model",
        "--effort",
        "high",
        "--sandbox",
    )
    assert json.loads(invocation.stdin) == {
        "event": "user",
        "message": {"content": "multi\nline"},
    }
    assert invocation.stdin.endswith(b"\n")


def test_antigravity_decodes_terminal_result_and_ignores_tool_steps() -> None:
    decoded = antigravity.decode(
        codex_stream(
            {"event": "future.before"},
            {"event": "init", "conversation_id": "id", "init": {"cwd": "/work"}},
            {
                "event": "step_update",
                "step_update": {"step_type": "tool", "tool_info": {"output": "secret"}},
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "answer",
                    "usage": antigravity_usage(),
                    "future": True,
                },
            },
            {"event": "future.after"},
        )
    )
    assert decoded.output == "answer"
    assert decoded.usage == Usage(
        input_tokens=10,
        cached_input_tokens=3,
        output_tokens=4,
        reasoning_output_tokens=2,
    )
    assert decoded.error is None


def test_antigravity_failure_preserves_safe_response_and_usage() -> None:
    decoded = antigravity.decode(
        codex_stream(
            {"event": "init", "init": {}},
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "response": "partial",
                    "error": "permission denied",
                    "usage": antigravity_usage(),
                },
            },
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "permission denied")


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            (
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "answer",
                        "usage": antigravity_usage(),
                    },
                },
                {"event": "init", "init": {}},
            ),
            "Antigravity result event is malformed.",
        ),
        (
            (
                {"event": "step_update", "step_update": {}},
                {"event": "init", "init": {}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "answer",
                        "usage": antigravity_usage(),
                    },
                },
            ),
            "Antigravity step_update event is malformed.",
        ),
        (
            (
                {"event": "init", "init": {}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "answer",
                        "usage": antigravity_usage(),
                    },
                },
                {"event": "step_update", "step_update": {}},
            ),
            "Antigravity step_update event is malformed.",
        ),
    ],
)
def test_antigravity_rejects_recognized_event_order(
    events: tuple[dict[str, object], ...], message: str
) -> None:
    decoded = antigravity.decode(codex_stream(*events))
    assert decoded.output == "answer"
    assert decoded.error == ResultError("protocol_error", message)


def test_antigravity_provider_failure_precedes_event_order_error() -> None:
    decoded = antigravity.decode(
        codex_stream(
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "response": "partial",
                    "error": "unavailable",
                    "usage": antigravity_usage(),
                },
            },
            {"event": "init", "init": {}},
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "unavailable")


@pytest.mark.parametrize(
    "stream",
    [
        "",
        "{\n",
        codex_stream({"event": "future"}),
        codex_stream({"event": "result", "result": {"status": "SUCCESS", "response": "x"}}),
        codex_stream(
            {"event": "init", "init": {}},
            {
                "event": "result",
                "result": {"status": "RUNNING", "response": "x", "usage": antigravity_usage()},
            },
        ),
        codex_stream(
            {"event": "init", "init": {}},
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "x",
                    "usage": antigravity_usage(output_tokens=-1),
                },
            },
        ),
    ],
)
def test_antigravity_rejects_malformed_truncated_and_unknown_only_streams(stream: str) -> None:
    decoded = antigravity.decode(stream)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"
