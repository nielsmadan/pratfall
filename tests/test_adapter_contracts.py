import json
from collections.abc import Callable
from types import ModuleType

import pytest

from adapter_helpers import (
    antigravity_usage,
    codex_stream,
    codex_usage,
    copilot_result,
    opencode_usage,
    resolved,
)
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
from pratfall.consumer import ByteConsumer, ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import DecodedOutput, Options, ResultError, Usage


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
    adapter: ModuleType, agent: str, arguments: tuple[str, ...]
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
    adapter: ModuleType, agent: str, arguments: tuple[str, ...], message: str
) -> None:
    with pytest.raises(PratError, match=message):
        adapter.build(resolved(agent, Options(native_args=arguments)), b"prompt")


@pytest.mark.parametrize("adapter", [kiro, hermes])
def test_text_adapters_preserve_stdout_and_remove_terminal_line_endings(
    adapter: ModuleType,
) -> None:
    decoded = adapter.decode("banner\nfinal answer\r\n")
    assert decoded == DecodedOutput(output="banner\nfinal answer")


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
    decoder: Callable[[str], DecodedOutput], payload: str
) -> None:
    decoded = decoder(payload)
    assert decoded.output == ""
    assert decoded.error is None


@pytest.mark.parametrize(
    ("factory", "stream"),
    [
        (
            codex.consumer,
            codex_stream(
                {
                    "type": "item.completed",
                    "item": {"id": "", "type": "agent_message", "text": "answer12345"},
                }
            ),
        ),
        (
            copilot.consumer,
            codex_stream(
                {
                    "type": "assistant.message",
                    "data": {"messageId": "", "content": "answer12345"},
                }
            ),
        ),
        (
            opencode.consumer,
            codex_stream(
                {
                    "type": "text",
                    "part": {
                        "id": "",
                        "type": "text",
                        "text": "answer12345",
                        "time": {"end": 1},
                    },
                }
            ),
        ),
    ],
)
def test_missing_terminal_diagnostic_counts_against_retained_state(
    factory: Callable[[ConsumerLimits], ByteConsumer], stream: str
) -> None:
    incremental = factory(ConsumerLimits(event_bytes=1024, state_bytes=11, records=10))
    incremental.feed(stream.encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    assert failure.value.decoded is not None
    assert failure.value.decoded.output == "answer12345"
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 11 bytes."
    )


@pytest.mark.parametrize(
    ("factory", "first", "rejected"),
    [
        (
            codex.consumer,
            {
                "type": "item.completed",
                "item": {"id": "", "type": "agent_message", "text": "12345"},
            },
            {
                "type": "item.completed",
                "item": {"id": "x", "type": "agent_message", "text": "6"},
            },
        ),
        (
            copilot.consumer,
            {"type": "assistant.message", "data": {"messageId": "", "content": "12345"}},
            {"type": "assistant.message", "data": {"messageId": "x", "content": "6"}},
        ),
        (
            opencode.consumer,
            {
                "type": "text",
                "part": {"id": "", "type": "text", "text": "12345", "time": {"end": 1}},
            },
            {
                "type": "text",
                "part": {"id": "x", "type": "text", "text": "6", "time": {"end": 1}},
            },
        ),
    ],
)
def test_prior_state_failure_survives_finalization_with_exhausted_budget(
    factory: Callable[[ConsumerLimits], ByteConsumer], first: object, rejected: object
) -> None:
    incremental = factory(ConsumerLimits(event_bytes=1024, state_bytes=5, records=10))
    incremental.feed(codex_stream(first).encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(codex_stream(rejected).encode())
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 5 bytes."
    )
    with pytest.raises(ConsumerFailure) as finalized:
        incremental.finish()
    assert finalized.value.error == failure.value.error
    assert finalized.value.decoded is not None
    assert finalized.value.decoded.output == "12345"


@pytest.mark.parametrize(
    ("decoder", "first", "rejected"),
    [
        (
            copilot.decode,
            {"type": "assistant.message", "data": {"messageId": "a", "content": "old"}},
            {"type": "assistant.message", "data": {"messageId": "b", "content": "x"}},
        ),
        (
            opencode.decode,
            {
                "type": "text",
                "part": {"id": "a", "type": "text", "text": "old", "time": {"end": 1}},
            },
            {
                "type": "text",
                "part": {"id": "b", "type": "text", "text": "x", "time": {"end": 1}},
            },
        ),
    ],
)
def test_rejected_new_message_does_not_publish_ordering_entry(
    decoder: object, first: object, rejected: object
) -> None:
    factory = copilot.consumer if decoder is copilot.decode else opencode.consumer
    incremental = factory(ConsumerLimits(event_bytes=1024, state_bytes=4, records=10))
    incremental.feed(codex_stream(first).encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(codex_stream(rejected).encode())
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 4 bytes."
    )
    with pytest.raises(ConsumerFailure) as finalized:
        incremental.finish()
    assert finalized.value.error == failure.value.error
    assert finalized.value.decoded is not None
    assert finalized.value.decoded.output == "old"


@pytest.mark.parametrize(
    ("factory", "stream", "expected"),
    [
        (
            codex.consumer,
            codex_stream(
                {
                    "type": "item.completed",
                    "item": {"id": "雪", "type": "agent_message", "text": "café 雪"},
                },
                {"type": "turn.completed", "usage": codex_usage()},
            ).rstrip("\n"),
            "café 雪",
        ),
        (
            copilot.consumer,
            codex_stream(
                {
                    "type": "assistant.message",
                    "data": {"messageId": "雪", "content": "café 雪"},
                },
                copilot_result(),
            ).rstrip("\n"),
            "café 雪",
        ),
        (
            antigravity.consumer,
            codex_stream(
                {"event": "init", "init": {}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "café 雪",
                        "usage": antigravity_usage(),
                    },
                },
            ).rstrip("\n"),
            "café 雪",
        ),
        (
            opencode.consumer,
            codex_stream(
                {
                    "type": "text",
                    "part": {
                        "id": "雪",
                        "type": "text",
                        "text": "café 雪",
                        "time": {"end": 1},
                    },
                },
                {
                    "type": "step_finish",
                    "part": {
                        "id": "step",
                        "type": "step-finish",
                        "reason": "stop",
                        "cost": 0,
                        "tokens": opencode_usage(),
                    },
                },
            ).rstrip("\n"),
            "café 雪",
        ),
    ],
)
def test_jsonl_consumers_accept_every_byte_boundary_and_final_record(
    factory: Callable[[], ByteConsumer], stream: str, expected: str
) -> None:
    assert "雪".encode() in stream.encode()
    incremental = factory()
    for byte in stream.encode("utf-8"):
        incremental.feed(bytes((byte,)))
    decoded = incremental.finish()
    assert decoded.output == expected
    assert decoded.error is None


def test_jsonl_event_bound_counts_blank_unknown_and_crlf_records() -> None:
    limits = ConsumerLimits(event_bytes=8, state_bytes=1024, records=10)
    accepted = codex.consumer(limits)
    accepted.feed(b"        \r\n")
    assert accepted.finish().error is not None

    oversized = codex.consumer(limits)
    with pytest.raises(ConsumerFailure) as failure:
        oversized.feed(b"         \n")
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent output event exceeded 8 bytes."
    )


def test_jsonl_state_replacement_refunds_prior_text_and_duplicate_records() -> None:
    limits = ConsumerLimits(event_bytes=1024, state_bytes=20, records=3)
    incremental = copilot.consumer(limits)
    incremental.feed(
        codex_stream(
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "id", "deltaContent": "1234567890"},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "id", "content": "x", "model": "m"},
            },
            {
                "type": "assistant.message",
                "data": {"messageId": "id", "content": "y", "model": "m"},
            },
            copilot_result(),
        ).encode()
    )
    decoded = incremental.finish()
    assert decoded.output == "y"
    assert decoded.reported_models == ("m",)
    assert decoded.error is None


def test_jsonl_bounds_retained_records_and_answer_join_separators() -> None:
    records = copilot.consumer(ConsumerLimits(event_bytes=1024, state_bytes=1024, records=1))
    with pytest.raises(ConsumerFailure) as failure:
        records.feed(
            codex_stream(
                {"type": "assistant.message", "data": {"messageId": "a", "content": ""}},
                {"type": "assistant.message", "data": {"messageId": "b", "content": ""}},
            ).encode()
        )
    assert failure.value.error.code == "stdout_limit_exceeded"

    answer = codex.consumer(ConsumerLimits(event_bytes=1024, state_bytes=6, records=10))
    answer.feed(
        codex_stream(
            {
                "type": "item.completed",
                "item": {"id": "", "type": "agent_message", "text": "aa"},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
            },
        ).encode()
    )
    with pytest.raises(ConsumerFailure) as failure:
        answer.feed(
            codex_stream(
                {
                    "type": "item.completed",
                    "item": {"id": "", "type": "agent_message", "text": "b"},
                },
            ).encode()
        )
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", "Agent retained output state exceeded 6 bytes."
    )


def test_jsonl_rejects_unpaired_surrogate_excessive_nesting_and_numeric_width() -> None:
    surrogate = codex.decode(
        '{"type":"item.completed","item":{"id":"x","type":"agent_message","text":"\\ud800"}}\n'
    )
    nested = codex.decode("[" * 2000 + "]" * 2000 + "\n")
    numeric = codex.decode('{"type":"future","value":' + "1" * 129 + "}\n")
    assert surrogate.error is not None and surrogate.error.code == "output_encoding"
    assert nested.error is not None and nested.error.code == "protocol_error"
    assert numeric.error is not None and numeric.error.code == "protocol_error"


def test_jsonl_total_discarded_trace_has_no_cumulative_cap() -> None:
    unknown = json.dumps({"type": "future", "discarded": "x" * (1024 * 1024)}) + "\n"
    decoded = codex.decode(
        unknown * 9 + codex_stream({"type": "turn.completed", "usage": codex_usage()})
    )
    assert decoded.output == ""
    assert decoded.error is None


def test_late_provider_failures_outrank_duplicate_terminal_protocol_errors() -> None:
    copilot_decoded = copilot.decode(codex_stream(copilot_result(), copilot_result(1)))
    antigravity_decoded = antigravity.decode(
        codex_stream(
            {"event": "init", "init": {}},
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "answer",
                    "usage": antigravity_usage(),
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "response": "later",
                    "error": "failed later",
                    "usage": antigravity_usage(),
                },
            },
        )
    )
    codex_incremental = codex.consumer()
    codex_incremental.feed(
        codex_stream(
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": "answer"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
            {"type": "turn.failed", "error": {"message": "failed later"}},
        ).encode()
    )
    codex_decoded = codex_incremental.finish()
    opencode_incremental = opencode.consumer()
    opencode_incremental.feed(
        codex_stream(
            {
                "type": "text",
                "part": {
                    "id": "answer",
                    "type": "text",
                    "text": "answer",
                    "time": {"end": 1},
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "id": "step",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.25,
                    "tokens": opencode_usage(),
                },
            },
            {
                "type": "error",
                "error": {"name": "ProviderError", "data": {"message": "failed later"}},
            },
        ).encode()
    )
    opencode_decoded = opencode_incremental.finish()
    assert copilot_decoded.error == ResultError(
        "provider_error", "Copilot reported an unsuccessful result."
    )
    assert antigravity_decoded.output == "answer"
    assert antigravity_decoded.error == ResultError("provider_error", "failed later")
    assert codex_decoded.output == "answer"
    assert codex_decoded.usage == Usage(
        input_tokens=24_901,
        cached_input_tokens=8_960,
        cache_write_input_tokens=0,
        output_tokens=8,
        reasoning_output_tokens=0,
    )
    assert codex_decoded.error == ResultError("provider_error", "failed later")
    assert opencode_decoded.output == "answer"
    assert opencode_decoded.usage == Usage(
        input_tokens=10,
        cached_input_tokens=3,
        cache_write_input_tokens=1,
        output_tokens=4,
        reasoning_output_tokens=2,
    )
    assert opencode_decoded.cost_usd == 0.25
    assert opencode_decoded.error == ResultError("provider_error", "failed later")
