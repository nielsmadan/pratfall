import json

import pytest

from adapter_helpers import resolved
from pratfall.adapters import gemini
from pratfall.models import Options, ResultError, Usage


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
