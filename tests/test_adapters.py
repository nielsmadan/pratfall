import json

import pytest

from pratfall.adapters import (
    antigravity,
    claude,
    codex,
    copilot,
    cursor,
    gemini,
    hermes,
    kiro,
    openclaw,
    opencode,
)
from pratfall.catalog import BY_NAME
from pratfall.errors import PratError
from pratfall.models import DecodedOutput, Options, ResolvedProfile, ResultError, Usage


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


def antigravity_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "input_tokens": 10,
        "output_tokens": 4,
        "thinking_tokens": 2,
        "cache_read_tokens": 3,
        "total_tokens": 14,
    }
    usage.update(changes)
    return usage


def copilot_result(exit_code: object = 0) -> dict[str, object]:
    return {
        "type": "result",
        "timestamp": "2026-09-09T12:00:00.000Z",
        "sessionId": "session",
        "exitCode": exit_code,
        "usage": {
            "premiumRequests": 1,
            "totalApiDurationMs": 100,
            "sessionDurationMs": 120,
            "codeChanges": {"linesAdded": 2, "linesRemoved": 1, "filesModified": 1},
        },
    }


def opencode_usage(**changes: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "total": 18,
        "input": 10,
        "output": 4,
        "reasoning": 2,
        "cache": {"read": 3, "write": 1},
    }
    usage.update(changes)
    return usage


def openclaw_result(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ok": True,
        "status": "ok",
        "final": "final answer",
        "payloads": [{"text": "final answer"}],
        "usage": {"input": 12, "output": 4, "total": 16},
    }
    value.update(changes)
    return value


def test_claude_builds_verified_stdin_invocation() -> None:
    invocation = claude.build(
        resolved(
            "claude",
            Options(
                model="claude-fable-5",
                effort="high",
                max_budget_usd=2.5,
                max_turns=4,
                fast=True,
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
        "--settings",
        '{"fastMode": true}',
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


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        ("claude", ("--model=native",)),
        ("claude", ("-pprint",)),
        ("claude", ("-rsession",)),
        ("claude", ("--output-format", "text")),
        ("claude", ("--settings", '{"fastMode": true}')),
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
    ("agent", "fast", "expected"),
    [
        ("claude", None, ()),
        ("claude", False, ("--settings", '{"fastMode": false}')),
        ("codex", None, ()),
        ("codex", True, ("-c", 'service_tier="priority"')),
    ],
)
def test_fast_mode_native_mapping(agent: str, fast: bool | None, expected: tuple[str, ...]) -> None:
    adapter = claude if agent == "claude" else codex
    invocation = adapter.build(resolved(agent, Options(fast=fast)), b"prompt")
    for item in expected:
        assert item in invocation.argv
    if fast is None:
        assert "service_tier" not in " ".join(invocation.argv)
        assert "--settings" not in invocation.argv


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
        "modelUsage": {"model-a": {"future": True}, "model-b": None},
        "total_cost_usd": 0,
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
    assert decoded.reported_models == ("model-a", "model-b")
    assert decoded.cost_usd == 0
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
        "usage": {"input_tokens": True, "output_tokens": 1},
        "modelUsage": {"failure-model": {"anything": "is ignored"}},
        "total_cost_usd": 1.25,
    }
    decoded = claude.decode(json.dumps(value))
    assert decoded.output == ""
    assert decoded.usage is None
    assert decoded.reported_models == ("failure-model",)
    assert decoded.cost_usd == 1.25
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


@pytest.mark.parametrize("native_cost", [True, -0.1, float("inf"), float("nan")])
def test_claude_rejects_malformed_native_cost(native_cost: object) -> None:
    decoded = claude.decode(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "answer",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "total_cost_usd": native_cost,
            }
        )
    )
    assert decoded.cost_usd is None
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


def test_claude_provider_failure_outranks_malformed_accounting() -> None:
    decoded = claude.decode(
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["provider failed"],
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "modelUsage": [],
                "total_cost_usd": -1,
            }
        )
    )
    assert decoded.error == ResultError("provider_error", "provider failed")


@pytest.mark.parametrize(
    ("field", "malformed"),
    [("type", "future"), ("subtype", 3), ("is_error", "false")],
)
def test_claude_malformed_envelope_preserves_valid_accounting(
    field: str, malformed: object
) -> None:
    value: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "answer",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {"model-a": {}, "model-b": {}},
        "total_cost_usd": 1.25,
    }
    value[field] = malformed
    decoded = claude.decode(json.dumps(value))
    assert decoded.reported_models == ("model-a", "model-b")
    assert decoded.cost_usd == 1.25
    assert decoded.error == ResultError("protocol_error", "Claude result envelope is malformed.")


def test_claude_rejects_huge_cost_without_numeric_conversion_failure() -> None:
    huge_cost = "1" + "0" * 4_000
    decoded = claude.decode(
        '{"type":"result","subtype":"success","is_error":false,"result":"answer",'
        '"usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":' + huge_cost + "}"
    )
    assert decoded.cost_usd is None
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


def test_reported_model_rejects_unpaired_surrogate_before_json_emission() -> None:
    decoded = claude.decode(
        '{"type":"result","subtype":"success","is_error":false,"result":"answer",'
        '"usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"\\ud800":{}}}'
    )
    assert decoded.reported_models is None
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"
    json.dumps(decoded.reported_models, ensure_ascii=False).encode("utf-8")


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


def test_gemini_builds_json_invocation_with_leading_dash_prompt_as_option_value() -> None:
    invocation = gemini.build(
        resolved(
            "gemini",
            Options(model="gemini-model", native_args=("--approval-mode", "plan")),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "gemini-wrapper",
        "native",
        "--output-format",
        "json",
        "--model",
        "gemini-model",
        "--approval-mode",
        "plan",
        "--prompt=-leading prompt",
    )
    assert invocation.stdin == b""


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


def test_copilot_builds_equals_prompt_and_native_soft_credit_limit() -> None:
    invocation = copilot.build(
        resolved(
            "copilot",
            Options(
                model="copilot-model",
                effort="xhigh",
                max_ai_credits=2.5,
                native_args=("--allow-tool", "shell(git status)"),
            ),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "copilot-wrapper",
        "native",
        "--output-format=json",
        "--model=copilot-model",
        "--effort=xhigh",
        "--max-ai-credits=2.5",
        "--allow-tool",
        "shell(git status)",
        "--prompt=-leading prompt",
    )
    assert invocation.stdin == b""


def test_copilot_builds_optional_variadic_native_arguments() -> None:
    native_args = (
        "--available-tools",
        "--allow-tool",
        "shell(git status)",
        "write",
        "--deny-url=https://example.com",
        "--sandbox",
    )
    invocation = copilot.build(resolved("copilot", Options(native_args=native_args)), b"prompt")
    assert invocation.argv == (
        "copilot-wrapper",
        "native",
        "--output-format=json",
        *native_args,
        "--prompt=prompt",
    )


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


def test_opencode_builds_verified_stdin_invocation() -> None:
    invocation = opencode.build(
        resolved(
            "opencode",
            Options(model="provider/model", effort="high", native_args=("--pure",)),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "opencode-wrapper",
        "native",
        "run",
        "--format",
        "json",
        "--model",
        "provider/model",
        "--variant",
        "high",
        "--pure",
    )
    assert invocation.stdin == b"-leading prompt"


def test_kiro_builds_protected_text_invocation() -> None:
    invocation = kiro.build(
        resolved(
            "kiro",
            Options(
                model="claude-sonnet-4",
                effort="xhigh",
                native_args=("--agent", "reviewer", "--trust-tools=read,grep"),
            ),
        ),
        b"--leading prompt",
    )
    assert invocation.argv == (
        "kiro-wrapper",
        "native",
        "chat",
        "--no-interactive",
        "--wrap",
        "never",
        "--model",
        "claude-sonnet-4",
        "--effort",
        "xhigh",
        "--agent",
        "reviewer",
        "--trust-tools=read,grep",
        "--",
        "--leading prompt",
    )
    assert invocation.stdin == b""


def test_openclaw_builds_embedded_stdin_invocation() -> None:
    invocation = openclaw.build(
        resolved(
            "openclaw",
            Options(
                model="provider/model",
                effort="adaptive",
                timeout=42.5,
                native_args=("--isolated", "--code-mode", "code"),
            ),
        ),
        b"multi\nline",
    )
    assert invocation.argv == (
        "openclaw-wrapper",
        "native",
        "agent",
        "exec",
        "--json",
        "--message-file",
        "-",
        "--model",
        "provider/model",
        "--thinking",
        "adaptive",
        "--timeout",
        "43",
        "--isolated",
        "--code-mode",
        "code",
    )
    assert invocation.stdin == b"multi\nline"


@pytest.mark.parametrize(("timeout", "native"), [(0.25, "1"), (1e6, "1000000")])
def test_openclaw_serializes_native_timeout_as_ceiling_plain_integer(
    timeout: float, native: str
) -> None:
    invocation = openclaw.build(resolved("openclaw", Options(timeout=timeout)), b"prompt")
    assert invocation.argv[-2:] == ("--timeout", native)


def test_openclaw_explicit_fallback_requires_model() -> None:
    with pytest.raises(PratError, match="fallback requires an explicit model"):
        openclaw.build(
            resolved("openclaw", Options(native_args=("--fallback", "provider/backup"))),
            b"prompt",
        )


def test_openclaw_accepts_repeatable_fallback_with_model() -> None:
    arguments = ("--fallback", "provider/backup", "--fallback=provider/last")
    invocation = openclaw.build(
        resolved("openclaw", Options(model="provider/primary", native_args=arguments)),
        b"prompt",
    )
    assert invocation.argv[-3:] == arguments


def test_hermes_builds_quiet_stdin_invocation_without_yolo() -> None:
    invocation = hermes.build(
        resolved(
            "hermes",
            Options(
                model="provider/model",
                effort="ultra",
                max_turns=7,
                native_args=("--provider", "provider", "--checkpoints"),
            ),
        ),
        b"-leading prompt",
    )
    assert invocation.argv == (
        "hermes-wrapper",
        "native",
        "chat",
        "--oneshot",
        "--quiet",
        "--query-file",
        "-",
        "--model",
        "provider/model",
        "--reasoning",
        "ultra",
        "--max-turns",
        "7",
        "--provider",
        "provider",
        "--checkpoints",
    )
    assert invocation.stdin == b"-leading prompt"
    assert "-z" not in invocation.argv


@pytest.mark.parametrize(
    ("adapter", "agent", "arguments"),
    [
        (gemini, "gemini", ("--prompt=native",)),
        (gemini, "gemini", ("-mnative",)),
        (antigravity, "antigravity", ("--print-timeout=2m",)),
        (antigravity, "antigravity", ("-ptext",)),
        (copilot, "copilot", ("--reasoning-effort=max",)),
        (copilot, "copilot", ("-Ctmp",)),
        (copilot, "copilot", ("--allow-tool", "write", "--prompt=native")),
        (cursor, "cursor", ("--resume=session",)),
        (cursor, "cursor", ("-ptext",)),
        (opencode, "opencode", ("--format=text",)),
        (opencode, "opencode", ("-msmall",)),
        (kiro, "kiro", ("--model=native",)),
        (kiro, "kiro", ("--output-format=stream-json",)),
        (kiro, "kiro", ("--resume-id=session",)),
        (openclaw, "openclaw", ("--message-file=task.md",)),
        (openclaw, "openclaw", ("--state-dir=/tmp/state",)),
        (openclaw, "openclaw", ("--timeout=0",)),
        (hermes, "hermes", ("--query=native",)),
        (hermes, "hermes", ("-zprompt",)),
        (hermes, "hermes", ("--max-turns=2",)),
    ],
)
def test_new_adapters_reject_owned_native_flags(
    adapter: object, agent: str, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(PratError, match="controlled by prat"):
        adapter.build(resolved(agent, Options(native_args=arguments)), b"prompt")


@pytest.mark.parametrize(
    ("adapter", "agent", "arguments", "message"),
    [
        (gemini, "gemini", ("--future",), "unknown native option"),
        (antigravity, "antigravity", ("resume",), "subcommands"),
        (copilot, "copilot", ("@args",), "response files"),
        (copilot, "copilot", ("--allow-tool", "write", "--future"), "unknown native option"),
        (copilot, "copilot", ("--allow-tool", "@args"), "response files"),
        (cursor, "cursor", ("--sandbox",), "missing native option value"),
        (opencode, "opencode", ("--pure=yes",), "does not take a value"),
        (kiro, "kiro", ("--future",), "unknown native option"),
        (openclaw, "openclaw", ("gateway",), "subcommands"),
        (hermes, "hermes", ("@args",), "response files"),
    ],
)
def test_new_adapters_reject_unvetted_native_arguments(
    adapter: object, agent: str, arguments: tuple[str, ...], message: str
) -> None:
    with pytest.raises(PratError, match=message):
        adapter.build(resolved(agent, Options(native_args=arguments)), b"prompt")


@pytest.mark.parametrize(
    "argument",
    ("--bash-env", "--bash-env=on", "--bash-envon", "--no-bash-env", "--no-bash-env=true"),
)
def test_copilot_rejects_native_flags_that_persist_configuration(argument: str) -> None:
    with pytest.raises(PratError, match="unknown native option"):
        copilot.build(resolved("copilot", Options(native_args=(argument,))), b"prompt")


@pytest.mark.parametrize("adapter", [kiro, hermes])
def test_text_adapters_preserve_stdout_and_remove_terminal_line_endings(adapter: object) -> None:
    decoded = adapter.decode("banner\nfinal answer\r\n")
    assert decoded == DecodedOutput(output="banner\nfinal answer")


def test_openclaw_decodes_stable_success_envelope() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                costUsd=0.0021,
                model="model",
                provider="provider",
                sessionId="session",
            )
        )
    )
    assert decoded.output == "final answer"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.reported_models == ("provider/model",)
    assert decoded.cost_usd == 0.0021
    assert decoded.error is None


def test_openclaw_preserves_partial_final_on_provider_failure() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                final="safe partial",
                payloads=[{"text": "safe partial"}],
                error={"message": "provider unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe partial"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("provider_error", "provider unavailable")


def test_openclaw_explicit_error_overrides_contradictory_success_and_malformed_usage() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                usage={"input": 1},
                error={"message": "provider unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage is None
    assert decoded.error == ResultError("provider_error", "provider unavailable")


def test_openclaw_error_payload_overrides_contradictory_success() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                payloads=[
                    {"text": "safe final", "future": {"additive": True}},
                    {"text": "model failed", "isError": True},
                ],
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("provider_error", "model failed")


def test_openclaw_preserves_final_when_success_error_field_is_malformed() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                error={"message": 3, "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("protocol_error", "OpenClaw error is malformed.")


def test_openclaw_provider_timeout_is_explicit() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="timeout",
                final="partial",
                error={"message": "deadline exceeded", "kind": "timeout"},
            )
        )
    )
    assert decoded.output == "partial"
    assert decoded.timed_out is True
    assert decoded.error == ResultError("timeout", "deadline exceeded")


def test_openclaw_omitted_usage_stays_unknown() -> None:
    value = openclaw_result()
    del value["usage"]
    decoded = openclaw.decode(json.dumps(value))
    assert decoded.usage is None
    assert decoded.error is None


def test_openclaw_model_without_provider_and_zero_cost_are_reported() -> None:
    decoded = openclaw.decode(json.dumps(openclaw_result(model="model", costUsd=0)))
    assert decoded.reported_models == ("model",)
    assert decoded.cost_usd == 0
    assert decoded.error is None


@pytest.mark.parametrize("provider", ["", 3])
def test_openclaw_malformed_provider_drops_model_but_preserves_cost(provider: object) -> None:
    decoded = openclaw.decode(
        json.dumps(openclaw_result(model="model", provider=provider, costUsd=0.25))
    )
    assert decoded.reported_models is None
    assert decoded.cost_usd == 0.25
    assert decoded.error == ResultError("protocol_error", "OpenClaw provider is malformed.")


def test_openclaw_provider_failure_preserves_accounting_and_outranks_malformed_cost() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                model="model",
                provider="provider",
                costUsd=True,
                error={"message": "unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.reported_models == ("provider/model",)
    assert decoded.cost_usd is None
    assert decoded.error == ResultError("provider_error", "unavailable")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        json.dumps({"future": True}),
        json.dumps(openclaw_result(status="future")),
        json.dumps(openclaw_result(status=[])),
        json.dumps(openclaw_result(status={})),
        json.dumps(openclaw_result(ok=False)),
        json.dumps(openclaw_result(final=None)),
        json.dumps(openclaw_result(payloads=None)),
        json.dumps(openclaw_result(payloads=[3])),
        json.dumps(openclaw_result(payloads=[{"text": 3}])),
        json.dumps(openclaw_result(payloads=[{"mediaUrl": 3}])),
        json.dumps(openclaw_result(payloads=[{"mediaUrls": ["image.png", 3]}])),
        json.dumps(openclaw_result(payloads=[{"isError": "yes"}])),
        json.dumps(openclaw_result(usage={"input": 1, "output": 2})),
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                error={"message": 3, "kind": "model_error"},
            )
        ),
        json.dumps(openclaw_result()) + "\n" + json.dumps(openclaw_result()),
    ],
)
def test_openclaw_rejects_malformed_or_truncated_results(payload: str) -> None:
    decoded = openclaw.decode(payload)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


def test_gemini_decodes_per_model_usage_without_double_counting_roles() -> None:
    model = {
        "api": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 10},
        "tokens": {
            "input": 7,
            "prompt": 10,
            "candidates": 4,
            "total": 16,
            "cached": 3,
            "thoughts": 2,
            "tool": 0,
        },
        "roles": {"assistant": {"tokens": {"candidates": 4}}},
    }
    decoded = gemini.decode(
        json.dumps(
            {
                "response": "final",
                "stats": {"models": {"model-a": model, "model-b": model}},
                "future": True,
            }
        )
    )
    assert decoded.output == "final"
    assert decoded.usage == Usage(
        input_tokens=14,
        cached_input_tokens=6,
        output_tokens=8,
        reasoning_output_tokens=4,
    )
    assert decoded.reported_models == ("model-a", "model-b")
    assert decoded.cost_usd is None
    assert decoded.error is None


def test_gemini_malformed_response_preserves_valid_reported_models() -> None:
    model = {
        "tokens": {
            "input": 7,
            "prompt": 10,
            "candidates": 4,
            "total": 16,
            "cached": 3,
            "thoughts": 2,
            "tool": 0,
        }
    }
    decoded = gemini.decode(
        json.dumps({"response": 3, "stats": {"models": {"model-a": model, "model-b": model}}})
    )
    assert decoded.reported_models == ("model-a", "model-b")
    assert decoded.error == ResultError(
        "protocol_error", "Gemini response must be a string when present."
    )


def test_gemini_preserves_partial_response_on_provider_error() -> None:
    decoded = gemini.decode(
        json.dumps(
            {
                "response": "partial",
                "error": {"type": "TimeoutError", "message": "request timed out", "code": 50},
            }
        )
    )
    assert decoded.output == "partial"
    assert decoded.usage is None
    assert decoded.error == ResultError("timeout", "request timed out")
    assert decoded.timed_out


def test_gemini_does_not_convert_malformed_timeout_error() -> None:
    decoded = gemini.decode('{"error":{"type":"TimeoutError"}}')
    assert decoded.error == ResultError("protocol_error", "Gemini error field is malformed.")
    assert not decoded.timed_out


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        "{}",
        '{"response":3}',
        '{"response":"ok","stats":[]}',
        '{"response":"ok","stats":{"models":{"m":{"tokens":{"input":true}}}}}',
        '{"error":{"type":"ApiError"}}',
    ],
)
def test_gemini_rejects_malformed_or_truncated_results(payload: str) -> None:
    decoded = gemini.decode(payload)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


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


def test_copilot_deduplicates_snapshots_and_excludes_subagent_text() -> None:
    decoded = copilot.decode(
        codex_stream(
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "one", "deltaContent": "part"},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "one", "content": "complete"},
            },
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "one", "deltaContent": "duplicate"},
            },
            {
                "type": "assistant.message",
                "agentId": "child",
                "data": {"messageId": "child", "content": "subagent"},
            },
            {
                "type": "session.error",
                "data": {"errorType": "model_call", "message": "recovered"},
            },
            copilot_result(),
        )
    )
    assert decoded.output == "complete"
    assert decoded.usage is None
    assert decoded.error is None


def test_copilot_reports_distinct_root_completed_models_only() -> None:
    decoded = copilot.decode(
        codex_stream(
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "one", "deltaContent": "draft", "model": []},
            },
            {
                "type": "assistant.message",
                "agentId": "child",
                "data": {"messageId": "child", "content": "hidden", "model": []},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "one", "content": "one", "model": "model-a"},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "two", "content": "two", "model": "model-b"},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "three", "content": "three", "model": "model-a"},
            },
            {"type": "session.config", "data": {"model": []}},
            copilot_result(),
        )
    )
    assert decoded.output == "one\ntwo\nthree"
    assert decoded.reported_models == ("model-a", "model-b")
    assert decoded.cost_usd is None
    assert decoded.error is None


def test_copilot_terminal_error_beats_protocol_error_and_preserves_text() -> None:
    decoded = copilot.decode(
        codex_stream(
            {"type": "assistant.message", "data": {"messageId": "one", "content": "partial"}},
            {"type": "assistant.message", "data": {}},
            {
                "type": "session.warning",
                "data": {"warningType": "policy_blocked", "message": "blocked by policy"},
            },
            copilot_result(),
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "blocked by policy")


@pytest.mark.parametrize(
    "stream",
    [
        "",
        "{\n",
        codex_stream({"type": "future.event"}),
        codex_stream({"type": "assistant.message", "data": {}}),
        codex_stream({**copilot_result(), "usage": {}}),
        codex_stream(copilot_result(exit_code=True)),
    ],
)
def test_copilot_rejects_malformed_truncated_and_unknown_only_streams(stream: str) -> None:
    decoded = copilot.decode(stream)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"


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
    assert decoded == DecodedOutput(output="final answer")


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


def test_opencode_deduplicates_parts_aggregates_steps_and_omits_other_events() -> None:
    decoded = opencode.decode(
        codex_stream(
            {"type": "reasoning", "part": {"id": "reason", "text": "private"}},
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "draft", "time": {"end": 1}},
            },
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "answer one", "time": {"end": 2}},
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "step-one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 0.01,
                    "tokens": opencode_usage(),
                },
            },
            {"type": "tool_use", "part": {"output": "tool output"}},
            {
                "type": "text",
                "part": {"id": "two", "type": "text", "text": "answer two", "time": {"end": 3}},
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "step-two",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.02,
                    "tokens": opencode_usage(
                        input=2, output=1, reasoning=0, cache={"read": 0, "write": 0}
                    ),
                },
            },
        )
    )
    assert decoded.output == "answer one\nanswer two"
    assert decoded.usage == Usage(12, 3, 1, 5, 2)
    assert decoded.reported_models is None
    assert decoded.cost_usd == pytest.approx(0.03)
    assert decoded.error is None


def test_opencode_repeated_usage_snapshot_replaces_previous_value() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 0.01,
                    "tokens": opencode_usage(input=20),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.01,
                    "tokens": opencode_usage(input=10),
                },
            },
        )
    )
    assert decoded.usage == Usage(10, 3, 1, 4, 2)
    assert decoded.cost_usd == 0.01
    assert decoded.error is None


def test_opencode_latest_cost_snapshot_replaces_and_distinct_steps_sum_once() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 10,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 1.25,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "two",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 2.75,
                    "tokens": opencode_usage(),
                },
            },
        )
    )
    assert decoded.cost_usd == 4
    assert decoded.error is None


def test_opencode_unknown_latest_step_cost_makes_aggregate_unknown() -> None:
    events = [
        {
            "type": "step_finish",
            "part": {
                "id": "one",
                "type": "step-finish",
                "reason": "tool-calls",
                "cost": 1,
                "tokens": opencode_usage(),
            },
        },
        {
            "type": "step_finish",
            "part": {
                "id": "two",
                "type": "step-finish",
                "reason": "stop",
                "cost": None,
                "tokens": opencode_usage(),
            },
        },
    ]
    decoded = opencode.decode(codex_stream(*events))
    assert decoded.cost_usd is None
    assert decoded.error is None
    events[-1]["part"].pop("cost")
    decoded = opencode.decode(codex_stream(*events))
    assert decoded.cost_usd is None
    assert decoded.error is None


def test_opencode_malformed_latest_cost_replaces_same_step_snapshot() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 1,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": False,
                    "tokens": opencode_usage(),
                },
            },
        )
    )
    assert decoded.cost_usd is None
    assert decoded.error == ResultError("protocol_error", "OpenCode step_finish cost is malformed.")


def test_opencode_malformed_distinct_step_cost_makes_aggregate_unknown() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 1,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "two",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": False,
                    "tokens": opencode_usage(),
                },
            },
        )
    )
    assert decoded.cost_usd is None
    assert decoded.error == ResultError("protocol_error", "OpenCode step_finish cost is malformed.")


def test_opencode_known_replacement_restores_total_after_malformed_cost() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 1,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "two",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": False,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "two",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 2,
                    "tokens": opencode_usage(),
                },
            },
        )
    )
    assert decoded.cost_usd == 3
    assert decoded.error == ResultError("protocol_error", "OpenCode step_finish cost is malformed.")


def test_opencode_rejects_aggregate_cost_overflow_without_raising() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "one",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 1e308,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "two",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 1e308,
                    "tokens": opencode_usage(),
                },
            },
        )
    )
    assert decoded.cost_usd is None
    assert decoded.error == ResultError("protocol_error", "OpenCode aggregate cost is malformed.")


def test_opencode_provider_failure_outranks_malformed_cost() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "error",
                    "cost": False,
                    "tokens": opencode_usage(),
                },
            }
        )
    )
    assert decoded.cost_usd is None
    assert decoded.error == ResultError(
        "provider_error", "OpenCode stopped with finish reason 'error'."
    )


def test_opencode_error_preserves_completed_text() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "partial", "time": {"end": 1}},
            },
            {
                "type": "error",
                "error": {"name": "ProviderError", "data": {"message": "unavailable"}},
            },
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "unavailable")


def test_opencode_accepts_authentic_message_less_output_length_error() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "partial", "time": {"end": 1}},
            },
            {
                "type": "error",
                "error": {"name": "MessageOutputLengthError", "data": {}},
            },
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "MessageOutputLengthError")


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        (gemini.decode, '{"response":""}'),
        (
            antigravity.decode,
            codex_stream(
                {"event": "init", "init": {}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "",
                        "usage": antigravity_usage(),
                    },
                },
            ),
        ),
        (copilot.decode, codex_stream(copilot_result())),
        (
            cursor.decode,
            '{"type":"result","subtype":"success","is_error":false,"result":""}',
        ),
        (
            opencode.decode,
            codex_stream(
                {
                    "type": "step_finish",
                    "part": {
                        "id": "step",
                        "type": "step-finish",
                        "reason": "stop",
                        "cost": 0.01,
                        "tokens": opencode_usage(),
                    },
                }
            ),
        ),
    ],
)
def test_new_structured_adapters_allow_empty_completed_output(
    decoder: object, payload: str
) -> None:
    decoded = decoder(payload)
    assert decoded.output == ""
    assert decoded.error is None


@pytest.mark.parametrize(
    "stream",
    [
        "",
        "{\n",
        codex_stream({"type": "future"}),
        codex_stream({"type": "text", "part": {"id": "x", "type": "text", "text": "partial"}}),
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "x",
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 0.01,
                    "tokens": opencode_usage(),
                },
            }
        ),
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "x",
                    "type": "step-finish",
                    "reason": "length",
                    "cost": 0.01,
                    "tokens": opencode_usage(),
                },
            }
        ),
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "x",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.01,
                    "tokens": opencode_usage(output=-1),
                },
            }
        ),
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "x",
                    "type": "step-finish",
                    "reason": [],
                    "cost": 0.01,
                    "tokens": opencode_usage(),
                },
            }
        ),
        codex_stream({"type": "error", "error": {}}),
    ],
)
def test_opencode_rejects_incomplete_and_malformed_streams(stream: str) -> None:
    decoded = opencode.decode(stream)
    assert decoded.error is not None
    assert decoded.error.code in {"protocol_error", "provider_error"}
