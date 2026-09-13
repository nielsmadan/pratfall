import json

import pytest

from adapter_helpers import resolved
from pratfall.adapters import claude
from pratfall.models import Options, ResultError, Usage


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
