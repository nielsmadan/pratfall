import json

import pytest

from adapter_helpers import resolved
from pratfall.adapters import claude
from pratfall.adapters.registry import ADAPTERS
from pratfall.consumer import ConsumerLimits
from pratfall.limits import JSON_DEPTH, RECORD_COUNT, RETAINED_STATE_BYTES
from pratfall.models import JsonValue, Options, ResultError, Usage


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


def _schema_result(answer: object) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": answer,
        "usage": {"input_tokens": 7, "output_tokens": 9},
        "modelUsage": {"schema-model": {}},
        "total_cost_usd": 0.125,
    }


@pytest.mark.parametrize("answer", [None, False, 0, "", "🦊", [], {}, [1, None], {"ok": True}])
@pytest.mark.parametrize("result", [None, "native prose"])
def test_claude_schema_answer_is_authoritative_without_string_result(
    answer: object, result: str | None
) -> None:
    value = _schema_result(answer)
    if result is not None:
        value["result"] = result
    text = json.dumps(value)
    decoded = claude.decode(text, schema=True)
    assert decoded == claude.decode_schema(text)
    assert decoded == ADAPTERS["claude"].for_schema(True).decode(text)
    assert decoded.error is None
    assert decoded.structured_output_present
    assert decoded.structured_output == answer
    assert decoded.output == json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    assert decoded.usage == Usage(input_tokens=7, output_tokens=9)
    assert decoded.reported_models == ("schema-model",)
    assert decoded.cost_usd == 0.125


def test_claude_schema_missing_answer_preserves_partial_text_and_accounting() -> None:
    value = _schema_result(None)
    value.pop("structured_output")
    value["result"] = "partial explanation"
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.output == "partial explanation"
    assert not decoded.structured_output_present
    assert decoded.usage == Usage(input_tokens=7, output_tokens=9)
    assert decoded.error == ResultError(
        "protocol_error", "Claude success is missing structured_output."
    )


@pytest.mark.parametrize(
    "answer, code", [(float("nan"), "protocol_error"), ("\ud800", "output_encoding")]
)
def test_claude_schema_rejects_malformed_answer(answer: object, code: str) -> None:
    value = _schema_result(answer)
    value["result"] = "safe partial text"
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.output == "safe partial text"
    assert decoded.error is not None and decoded.error.code == code
    assert not decoded.structured_output_present
    assert decoded.cost_usd == 0.125


def test_claude_schema_provider_retry_failure_preserves_error_and_accounting() -> None:
    value = _schema_result({"irrelevant": True})
    value.update(
        subtype="error_max_structured_output_retries", is_error=True, errors=["retries exhausted"]
    )
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.error == ResultError("provider_error", "retries exhausted")
    assert decoded.cost_usd == 0.125
    assert decoded.usage == Usage(input_tokens=7, output_tokens=9)
    assert not decoded.structured_output_present


def test_claude_schema_counts_encoded_answer_twice() -> None:
    value = _schema_result("a" * (RETAINED_STATE_BYTES // 2))
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.error is not None
    assert decoded.error.code == "stdout_limit_exceeded"
    assert decoded.output == ""
    assert decoded.cost_usd == 0.125


def test_claude_schema_retains_only_models_within_record_limit() -> None:
    value = _schema_result(None)
    models = tuple(f"model-{index}" for index in range(RECORD_COUNT + 1))
    value["modelUsage"] = {model: {} for model in models}
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.error == ResultError(
        "stdout_limit_exceeded", f"Agent retained output records exceeded {RECORD_COUNT}."
    )
    assert decoded.reported_models == models[:RECORD_COUNT]
    assert decoded.usage == Usage(input_tokens=7, output_tokens=9)
    assert decoded.cost_usd == 0.125
    assert not decoded.structured_output_present


@pytest.mark.parametrize(
    "state_bytes, expected_usage, expected_cost",
    [
        (1, None, None),
        (2, Usage(input_tokens=7, output_tokens=9), None),
        (7, Usage(input_tokens=7, output_tokens=9), 0.125),
    ],
)
def test_claude_schema_retains_only_accepted_accounting(
    monkeypatch: pytest.MonkeyPatch,
    state_bytes: int,
    expected_usage: Usage | None,
    expected_cost: float | None,
) -> None:
    monkeypatch.setattr(claude, "ConsumerLimits", lambda: ConsumerLimits(state_bytes=state_bytes))
    decoded = claude.decode_schema(json.dumps(_schema_result(None)))
    assert decoded.error == ResultError(
        "stdout_limit_exceeded", f"Agent retained output state exceeded {state_bytes} bytes."
    )
    assert decoded.usage == expected_usage
    assert decoded.cost_usd == expected_cost
    assert decoded.reported_models is None
    assert not decoded.structured_output_present


def test_claude_schema_drops_rejected_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude, "ConsumerLimits", lambda: ConsumerLimits(state_bytes=17))
    value = _schema_result(None)
    value["usage"] = None
    decoded = claude.decode_schema(json.dumps(value))
    assert decoded.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 17 bytes."
    )
    assert decoded.usage is None
    assert decoded.cost_usd == 0.125
    assert decoded.reported_models == ("schema-model",)
    assert not decoded.structured_output_present


def test_claude_schema_bounds_nesting_before_emission() -> None:
    answer: JsonValue = None
    for _ in range(JSON_DEPTH + 1):
        answer = [answer]
    decoded = claude.decode_schema(json.dumps(_schema_result(answer)))
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"
    assert "nesting" in decoded.error.message
    assert not decoded.structured_output_present


def test_claude_ordinary_output_ignores_structured_payload() -> None:
    value = _schema_result({"ok": True})
    value["result"] = "ordinary text"
    decoded = claude.decode(json.dumps(value))
    assert decoded.output == "ordinary text"
    assert decoded.error is None
    assert decoded.structured_output is None
    assert not decoded.structured_output_present


def test_claude_passes_the_session_label_through() -> None:
    invocation = claude.build(
        resolved("claude", Options(native_args=("--name", "feed/daily:2026-09-19T07-00"))),
        b"prompt",
    )
    assert invocation.argv[-2:] == ("--name", "feed/daily:2026-09-19T07-00")
    claude.validate(resolved("claude", Options(native_args=("--name", "label"))))


def test_claude_reports_the_native_session_id() -> None:
    value = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "final answer",
        "session_id": "4ebf82be-4b4b-4642-9e5a-654c4cd58642",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    assert claude.decode(json.dumps(value)).session_id == "4ebf82be-4b4b-4642-9e5a-654c4cd58642"


@pytest.mark.parametrize("session", [None, 7, "", "a\0b"])
def test_claude_ignores_a_malformed_session_id(session: object) -> None:
    value = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "final answer",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    if session is not None:
        value["session_id"] = session
    decoded = claude.decode(json.dumps(value))
    assert decoded.session_id is None
    assert decoded.output == "final answer"
