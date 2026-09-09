import json

import pytest

from pratfall.adapters import claude, codex
from pratfall.catalog import BY_NAME
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, Usage


def resolved(agent: str, options: Options | None = None) -> ResolvedProfile:
    return ResolvedProfile(
        BY_NAME[agent], "test", (f"{agent}-wrapper", "native"), options or Options()
    )


def codex_stream(*events: object) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def codex_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "input_tokens": 24_901,
        "cached_input_tokens": 8_960,
        "cache_write_input_tokens": 0,
        "output_tokens": 8,
        "reasoning_output_tokens": 0,
    }
    usage.update(changes)
    return usage


def test_claude_builds_verified_stdin_invocation() -> None:
    invocation = claude.build(
        resolved(
            "claude",
            Options(
                model="claude-fable-5",
                effort="high",
                max_budget_usd=2.5,
                max_turns=4,
                native_args=("--permission-mode=plan", "--allowed-tools", "Read,Glob"),
            ),
        ),
        b"prompt\n",
    )
    assert invocation.argv == (
        "claude-wrapper",
        "native",
        "-p",
        "--output-format",
        "json",
        "--model",
        "claude-fable-5",
        "--effort",
        "high",
        "--max-budget-usd",
        "2.5",
        "--max-turns",
        "4",
        "--permission-mode=plan",
        "--allowed-tools",
        "Read,Glob",
    )
    assert invocation.stdin == b"prompt\n"


def test_codex_builds_verified_stdin_invocation_and_toml_quotes_effort() -> None:
    invocation = codex.build(
        resolved(
            "codex",
            Options(
                model="gpt-5.6-luna",
                effort='high"value',
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
        "--sandbox",
        "read-only",
        "--ephemeral",
        "-",
    )
    assert invocation.stdin == b"-leading prompt"


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        ("claude", ("--model=native",)),
        ("claude", ("-pprint",)),
        ("claude", ("-rsession",)),
        ("claude", ("--output-format", "text")),
        ("codex", ("-mnative",)),
        ("codex", ("--cd=/tmp",)),
        ("codex", ("-cmodel=other",)),
        ("codex", ("--json",)),
    ],
)
def test_owned_native_flags_are_rejected(agent: str, arguments: tuple[str, ...]) -> None:
    adapter = claude if agent == "claude" else codex
    with pytest.raises(PratError, match="controlled by prat"):
        adapter.build(resolved(agent, Options(native_args=arguments)), b"prompt")


@pytest.mark.parametrize(
    ("agent", "arguments", "message"),
    [
        ("claude", ("positional",), "positional arguments"),
        ("codex", ("resume",), "subcommands"),
        ("claude", ("@response",), "response files"),
        ("codex", ("--future-flag",), "unknown native option"),
        ("codex", ("--sandbox",), "missing native option value"),
        ("claude", ("--verbose=yes",), "does not take a value"),
        ("codex", ("--sandbox", "--model=native"), "use the =VALUE form"),
    ],
)
def test_unvetted_native_argument_forms_are_rejected(
    agent: str, arguments: tuple[str, ...], message: str
) -> None:
    adapter = claude if agent == "claude" else codex
    with pytest.raises(PratError, match=message):
        adapter.build(resolved(agent, Options(native_args=arguments)), b"prompt")


def test_claude_decodes_primary_source_result_shape() -> None:
    value = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "final answer",
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
            "output_tokens": 4,
        },
        "modelUsage": {"future": {"additive": True}},
        "terminal_reason": "completed",
    }
    decoded = claude.decode(json.dumps(value))
    assert decoded.output == "final answer"
    assert decoded.usage == Usage(
        input_tokens=10,
        cached_input_tokens=3,
        cache_write_input_tokens=2,
        output_tokens=4,
    )
    assert decoded.error is None


@pytest.mark.parametrize(
    "subtype",
    [
        "error_max_turns",
        "error_during_execution",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    ],
)
def test_claude_decodes_authentic_error_result_variants(subtype: str) -> None:
    value = {
        "type": "result",
        "subtype": subtype,
        "is_error": True,
        "errors": ["First native diagnostic.", "Second native diagnostic."],
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    decoded = claude.decode(json.dumps(value))
    assert decoded.output == ""
    assert decoded.usage == Usage(input_tokens=10, output_tokens=1)
    assert decoded.error is not None
    assert decoded.error.code == "provider_error"
    assert decoded.error.message == "First native diagnostic.\nSecond native diagnostic."


@pytest.mark.parametrize("errors", [None, "failure", ["failure", 3]])
def test_claude_rejects_malformed_recognized_error_fields(errors: object) -> None:
    value = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    if errors is not None:
        value["errors"] = errors
    decoded = claude.decode(json.dumps(value))
    assert decoded.usage == Usage(input_tokens=10, output_tokens=1)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        '{"type":"result","subtype":"success","is_error":false,"result":4,"usage":{}}',
        '{"type":"result","subtype":"future","is_error":true,"usage":'
        '{"input_tokens":1,"output_tokens":1}}',
        '{"type":"result","subtype":"success","is_error":false,"result":"ok"}',
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"usage":{"input_tokens":true,"output_tokens":1}}',
        "{}\n{}",
    ],
)
def test_claude_rejects_malformed_or_truncated_results(payload: str) -> None:
    decoded = claude.decode(payload)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


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
