import pytest

from adapter_helpers import codex_stream, copilot_result, resolved
from pratfall.adapters import copilot
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, ResultError


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


@pytest.mark.parametrize(
    "argument",
    ("--bash-env", "--bash-env=on", "--bash-envon", "--no-bash-env", "--no-bash-env=true"),
)
def test_copilot_rejects_native_flags_that_persist_configuration(argument: str) -> None:
    with pytest.raises(PratError, match="unknown native option"):
        copilot.build(resolved("copilot", Options(native_args=(argument,))), b"prompt")


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


def test_copilot_many_tiny_and_empty_deltas_share_one_logical_record() -> None:
    incremental = copilot.consumer(ConsumerLimits(event_bytes=1024, state_bytes=5000, records=2))
    events: list[dict[str, object]] = [
        {
            "type": "assistant.message_delta",
            "data": {"messageId": "one", "deltaContent": "x" if index % 2 else ""},
        }
        for index in range(4000)
    ]
    events.append(copilot_result())
    incremental.feed(codex_stream(*events).encode())
    decoded = incremental.finish()
    assert decoded.output == "x" * 2000
    assert decoded.error is None


def test_copilot_answer_join_separator_respects_state_byte_limit() -> None:
    incremental = copilot.consumer(ConsumerLimits(event_bytes=1024, state_bytes=5, records=3))
    incremental.feed(
        codex_stream(
            {"type": "assistant.message", "data": {"messageId": "a", "content": "aa"}}
        ).encode()
    )
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(
            codex_stream(
                {"type": "assistant.message", "data": {"messageId": "b", "content": "b"}}
            ).encode()
        )
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 5 bytes."
    )
