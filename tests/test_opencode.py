import pytest

from adapter_helpers import codex_stream, opencode_usage, resolved
from pratfall.adapters import opencode
from pratfall.models import Options, ResultError, Usage


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
    events: list[dict[str, object]] = [
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
    part = events[-1]["part"]
    assert isinstance(part, dict)
    part.pop("cost")
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


@pytest.mark.parametrize(
    "stream",
    [
        "",
        codex_stream({"type": "future"}),
        codex_stream(
            {"type": "error", "error": {"name": "ProviderError", "data": {"message": "x"}}}
        ),
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0,
                    "tokens": opencode_usage(output=-1),
                },
            }
        ),
    ],
)
def test_opencode_unknown_or_invalid_usage_remains_unknown(stream: str) -> None:
    assert opencode.decode(stream).usage is None


def test_opencode_real_zero_usage_snapshot_is_reported() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0,
                    "tokens": opencode_usage(
                        total=0,
                        input=0,
                        output=0,
                        reasoning=0,
                        cache={"read": 0, "write": 0},
                    ),
                },
            }
        )
    )
    assert decoded.usage == Usage(0, 0, 0, 0, 0)
    assert decoded.error is None
