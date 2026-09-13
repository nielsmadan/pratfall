import pytest

from adapter_helpers import codex_stream, codex_usage, resolved
from pratfall.adapters import codex
from pratfall.models import Options, ResultError, Usage


def test_codex_builds_verified_stdin_invocation_and_toml_quotes_effort() -> None:
    invocation = codex.build(
        resolved(
            "codex",
            Options(
                model="gpt-5.6-luna",
                effort='high"value',
                fast=False,
                native_args=("--sandbox", "read-only", "--ephemeral"),
            ),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "codex-wrapper",
        "native",
        "exec",
        "--json",
        "--model",
        "gpt-5.6-luna",
        "-c",
        'model_reasoning_effort="high\\"value"',
        "-c",
        'service_tier="default"',
        "--sandbox",
        "read-only",
        "--ephemeral",
        "-",
    )
    assert invocation.stdin == b"-leading prompt"


def test_codex_decodes_sanitized_live_event_shape() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "thread.started", "thread_id": "thread"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "PRAT_NATIVE_OK"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
        )
    )
    assert decoded.output == "PRAT_NATIVE_OK"
    assert decoded.usage == Usage(24_901, 8_960, 0, 8, 0)
    assert decoded.error is None


def test_codex_selects_final_completed_message_per_turn() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "first", "type": "agent_message", "text": "commentary"},
            },
            {
                "type": "item.completed",
                "item": {"id": "reasoning", "type": "reasoning", "text": "private"},
            },
            {
                "type": "item.completed",
                "item": {"id": "final", "type": "agent_message", "text": "answer one"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
            {"type": "turn.started", "future": True},
            {
                "type": "item.completed",
                "item": {"id": "second", "type": "agent_message", "text": "answer two"},
            },
            {"type": "turn.completed", "usage": codex_usage(output_tokens=9)},
        )
    )
    assert decoded.output == "answer one\nanswer two"
    assert decoded.usage is not None
    assert decoded.usage.output_tokens == 9


def test_codex_success_can_have_empty_final_text_and_additive_unknown_events() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "future.event", "payload": {"unknown": True}},
            {"type": "turn.completed", "usage": {**codex_usage(), "future": 1}},
        )
    )
    assert decoded.output == ""
    assert decoded.error is None


def test_codex_absent_new_usage_fields_remain_unknown() -> None:
    usage = codex_usage()
    del usage["cache_write_input_tokens"]
    del usage["reasoning_output_tokens"]
    decoded = codex.decode(codex_stream({"type": "turn.completed", "usage": usage}))
    assert decoded.usage is not None
    assert decoded.usage.cache_write_input_tokens is None
    assert decoded.usage.reasoning_output_tokens is None


def test_codex_truncated_second_turn_invalidates_earlier_completion() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "first", "type": "agent_message", "text": "first answer"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
            {"type": "turn.started"},
        )
    )
    assert decoded.output == "first answer"
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


def test_codex_incomplete_turn_keeps_latest_completed_assistant_message_once() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "a", "type": "agent_message", "text": "A"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "b-draft", "type": "agent_message", "text": "draft"},
            },
            {
                "type": "item.completed",
                "item": {"id": "b", "type": "agent_message", "text": "B"},
            },
        )
    )
    assert decoded.output == "A\nB"
    assert decoded.error == ResultError(
        "protocol_error", "Codex stream ended without turn.completed."
    )


def test_codex_new_assistant_item_without_turn_started_requires_completion() -> None:
    decoded = codex.decode(
        codex_stream(
            {
                "type": "item.completed",
                "item": {"id": "a", "type": "agent_message", "text": "A"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
            {
                "type": "item.completed",
                "item": {"id": "b", "type": "agent_message", "text": "B"},
            },
        )
    )
    assert decoded.output == "A\nB"
    assert decoded.error == ResultError(
        "protocol_error", "Codex stream ended without turn.completed."
    )


def test_codex_provider_failure_outranks_protocol_failure_and_preserves_answer() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": "partial"},
            },
        )
        + "truncated{\n"
        + codex_stream({"type": "turn.failed", "error": {"message": "provider failed"}})
    )
    assert decoded.output == "partial"
    assert decoded.error is not None
    assert decoded.error.code == "provider_error"
    assert decoded.error.message == "provider failed"


@pytest.mark.parametrize(
    "stream",
    [
        "",
        "{\n",
        codex_stream({"type": "future.event"}),
        codex_stream({"type": "thread.started"}),
        codex_stream({"type": "item.completed", "item": {"id": "x"}}),
        codex_stream(
            {
                "type": "item.completed",
                "item": {"id": "x", "type": "agent_message", "text": 3},
            }
        ),
        codex_stream({"type": "turn.completed", "usage": codex_usage(output_tokens=-1)}),
        codex_stream({"type": "turn.failed", "error": {}}),
        codex_stream({"message": "missing type"}),
    ],
)
def test_codex_rejects_malformed_truncated_and_unknown_only_streams(stream: str) -> None:
    decoded = codex.decode(stream)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


def test_codex_top_level_error_is_provider_failure() -> None:
    decoded = codex.decode(codex_stream({"type": "error", "message": "API unavailable"}))
    assert decoded.error == decoded.error
    assert decoded.error is not None
    assert decoded.error.code == "provider_error"
    assert decoded.error.message == "API unavailable"
