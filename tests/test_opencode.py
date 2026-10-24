import pytest

from adapter_helpers import codex_stream, opencode_usage, resolved
from pratfall.adapters import opencode
from pratfall.adapters.registry import ADAPTERS, Adapter
from pratfall.consumer import ConsumerFactory, ConsumerFailure, ConsumerLimits, decode_with
from pratfall.errors import PratError
from pratfall.models import Invocation, Options, ResultError, Usage


def _versioned(version: str) -> Adapter:
    for_version = ADAPTERS["opencode"].for_version
    assert for_version is not None
    return for_version(version)


def _versioned_build(options: Options, version: str) -> Invocation:
    return _versioned(version).build(resolved("opencode", options), b"task")


_TOOL: dict[str, object] = {"type": "tool_use", "part": {"id": "tool", "type": "tool"}}
_PREAMBLE: dict[str, object] = {
    "type": "text",
    "part": {"id": "preamble", "type": "text", "text": "Let me check.", "time": {"end": 1}},
}


def _unfinished_final_step(
    *final: dict[str, object], first: tuple[dict[str, object], ...] = (_TOOL,)
) -> str:
    return codex_stream(
        {"type": "step_start", "part": {"id": "s1", "type": "step-start"}},
        *first,
        {
            "type": "step_finish",
            "part": {
                "id": "f1",
                "type": "step-finish",
                "reason": "tool-calls",
                "cost": 0.5,
                "tokens": opencode_usage(output=3),
            },
        },
        {"type": "step_start", "part": {"id": "s2", "type": "step-start"}},
        *final,
    )


_FINAL_TEXT: dict[str, object] = {
    "type": "text",
    "part": {"id": "answer", "type": "text", "text": "PRAT_OK", "time": {"end": 1}},
}


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


@pytest.mark.parametrize("version", ["1.19.3", "opencode opaque version 1.0"])
def test_opencode_one_keeps_variant_flag_invocation(version: str) -> None:
    options = Options(model="provider/model", effort="high", attachments=("/a.txt",))
    assert _versioned_build(options, version).argv == (
        "opencode-wrapper",
        "native",
        "run",
        "--format",
        "json",
        "--model",
        "provider/model",
        "--variant",
        "high",
        "--file=/a.txt",
    )


def test_opencode_two_runs_standalone_with_effort_as_model_variant() -> None:
    options = Options(
        model="provider/model",
        effort="high",
        attachments=("/a.txt",),
        native_args=("--auto",),
    )
    invocation = _versioned_build(options, "opencode v2.0.32")
    assert invocation.argv == (
        "opencode-wrapper",
        "native",
        "run",
        "--standalone",
        "--format",
        "json",
        "--model",
        "provider/model#high",
        "--file=/a.txt",
        "--auto",
    )
    assert invocation.stdin == b"task"


@pytest.mark.parametrize(
    ("options", "argv"),
    [
        (Options(), ()),
        (Options(model="provider/model#max"), ("--model", "provider/model#max")),
    ],
)
def test_opencode_two_passes_model_without_effort_unchanged(
    options: Options, argv: tuple[str, ...]
) -> None:
    invocation = _versioned_build(options, "2.1.0")
    assert invocation.argv[2:] == ("run", "--standalone", "--format", "json", *argv)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (Options(effort="high"), "set a model as well"),
        (Options(model="provider/model#max", effort="high"), "already names a #variant"),
    ],
)
def test_opencode_two_rejects_effort_it_cannot_express(options: Options, message: str) -> None:
    with pytest.raises(PratError, match=message) as error:
        _versioned("2.0.32").validate(resolved("opencode", options))
    assert (error.value.code, error.value.option) == ("invalid_arguments", "effort")


@pytest.mark.parametrize("flag", ["--pure", "--log-level=DEBUG"])
def test_opencode_two_rejects_flags_only_one_accepts(flag: str) -> None:
    options = Options(native_args=(flag,))
    _versioned("1.19.3").validate(resolved("opencode", options))
    with pytest.raises(PratError, match="unknown native option"):
        _versioned("2.0.32").validate(resolved("opencode", options))


@pytest.mark.parametrize("version", ["opencode dev", "build 20261018", "build .5.3"])
def test_opencode_rejects_unrecognized_version_text(version: str) -> None:
    with pytest.raises(PratError, match="version output is not recognized") as error:
        _versioned(version)
    assert error.value.code == "protocol_error"


@pytest.mark.parametrize("flag", ["--standalone", "--server=http://127.0.0.1:4096"])
def test_opencode_reserves_server_selection(flag: str) -> None:
    with pytest.raises(PratError, match="controlled by prat"):
        opencode.validate(resolved("opencode", Options(native_args=(flag,))))


def test_opencode_two_error_reports_native_message() -> None:
    decoded = opencode.decode(
        codex_stream(
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "partial", "time": {"end": 1}},
            },
            {
                "type": "error",
                "timestamp": 1792361711501,
                "sessionID": "",
                "error": {"type": "unknown", "message": "UnexpectedStatus: 500"},
            },
        )
    )
    assert decoded.output == "partial"
    assert decoded.error == ResultError("provider_error", "UnexpectedStatus: 500")


def test_opencode_two_error_with_empty_message_uses_generic_text() -> None:
    decoded = opencode.decode(
        codex_stream({"type": "error", "error": {"type": "aborted", "message": ""}})
    )
    assert decoded.error == ResultError("provider_error", "OpenCode reported an error.")


@pytest.mark.parametrize("error", [{"type": "unknown"}, {"type": "unknown", "message": 5}])
def test_opencode_two_error_without_string_message_is_malformed(error: dict[str, object]) -> None:
    decoded = opencode.decode(codex_stream({"type": "error", "error": error}))
    assert decoded.error == ResultError("protocol_error", "OpenCode error event is malformed.")


def test_opencode_two_accepts_answer_whose_final_step_finish_was_dropped() -> None:
    decoded = decode_with(opencode.v2_consumer, _unfinished_final_step(_FINAL_TEXT))
    assert decoded.output == "PRAT_OK"
    assert decoded.error is None
    assert decoded.usage is None
    assert decoded.cost_usd is None


def test_opencode_two_reports_usage_when_final_step_finish_arrives() -> None:
    final: dict[str, object] = {
        "type": "step_finish",
        "part": {
            "id": "f2",
            "type": "step-finish",
            "reason": "stop",
            "cost": 0.25,
            "tokens": opencode_usage(output=4),
        },
    }
    decoded = decode_with(opencode.v2_consumer, _unfinished_final_step(_FINAL_TEXT, final))
    assert decoded.error is None
    assert decoded.usage is not None
    assert decoded.usage.output_tokens == 7
    assert decoded.cost_usd == 0.75


@pytest.mark.parametrize(
    ("factory", "first", "final"),
    [
        (opencode.v2_consumer, (_TOOL,), ()),
        (opencode.v2_consumer, (_TOOL, _PREAMBLE), ()),
        (opencode.v2_consumer, (_TOOL,), (_FINAL_TEXT, _TOOL)),
        (opencode.consumer, (_TOOL,), (_FINAL_TEXT,)),
    ],
)
def test_opencode_unfinished_final_step_is_incomplete(
    factory: ConsumerFactory,
    first: tuple[dict[str, object], ...],
    final: tuple[dict[str, object], ...],
) -> None:
    decoded = decode_with(factory, _unfinished_final_step(*final, first=first))
    assert decoded.error == ResultError(
        "protocol_error", "OpenCode stream ended without a stop finish."
    )


def test_opencode_two_provider_error_outranks_dropped_final_step_finish() -> None:
    error: dict[str, object] = {
        "type": "error",
        "error": {"type": "unknown", "message": "UnexpectedStatus: 500"},
    }
    decoded = decode_with(opencode.v2_consumer, _unfinished_final_step(_FINAL_TEXT, error))
    assert decoded.output == "PRAT_OK"
    assert decoded.error == ResultError("provider_error", "UnexpectedStatus: 500")


def test_opencode_tool_use_reports_tool_activity() -> None:
    assert opencode.consumer().apply({"type": "tool_use", "part": {}}) == "tool"


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


def test_opencode_text_budget_failure_keeps_prior_parts() -> None:
    consumer = opencode.consumer(ConsumerLimits(state_bytes=64))
    consumer.feed(
        codex_stream(
            {
                "type": "text",
                "part": {"id": "one", "type": "text", "text": "partial", "time": {"end": 1}},
            }
        ).encode()
    )
    with pytest.raises(ConsumerFailure):
        consumer.feed(
            codex_stream(
                {
                    "type": "text",
                    "part": {"id": "two", "type": "text", "text": "x" * 100, "time": {"end": 2}},
                }
            ).encode()
        )
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"
    assert failure.value.decoded is not None and failure.value.decoded.output == "partial"


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
