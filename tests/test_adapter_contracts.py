import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from difflib import SequenceMatcher
from importlib import import_module
from typing import Protocol, get_args

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
    droid,
    gemini,
    hermes,
    kiro,
    openclaw,
    opencode,
    reasonix,
    vibe,
)
from pratfall.adapters.native_args import Flag
from pratfall.adapters.registry import ADAPTERS, Adapter
from pratfall.catalog import BY_NAME
from pratfall.cli.presentation import ACTIVITY_LABELS
from pratfall.codes import Code
from pratfall.consumer import (
    ByteConsumer,
    ConsumerFactory,
    ConsumerFailure,
    ConsumerLimits,
    decode_with,
)
from pratfall.errors import PratError
from pratfall.limits import STDOUT_BYTES
from pratfall.models import (
    Activity,
    Capabilities,
    DecodedOutput,
    Invocation,
    Options,
    ResolvedProfile,
    ResultError,
    Usage,
)


class AdapterModule(Protocol):
    def build(self, resolved: ResolvedProfile, prompt: bytes) -> Invocation: ...

    def validate(self, resolved: ResolvedProfile) -> None: ...

    def decode(self, stdout: str) -> DecodedOutput: ...


_TIMEOUT = 30.0
_FAST_MARKERS: Mapping[str, str] = {
    "claude": '{"fastMode": true}',
    "codex": 'service_tier="priority"',
}
_CAPABILITY_CASES: tuple[tuple[str, Options, str | Mapping[str, str]], ...] = (
    ("model", Options(timeout=_TIMEOUT, model="conformance-model"), "conformance-model"),
    ("effort", Options(timeout=_TIMEOUT, effort="conformance-effort"), "conformance-effort"),
    ("max_budget_usd", Options(timeout=_TIMEOUT, max_budget_usd=12.5), "12.5"),
    ("max_turns", Options(timeout=_TIMEOUT, max_turns=97), "97"),
    ("max_ai_credits", Options(timeout=_TIMEOUT, max_ai_credits=13.75), "13.75"),
    ("fast", Options(timeout=_TIMEOUT, fast=True), _FAST_MARKERS),
)
_EVERY_CAPABILITY = Options(
    timeout=_TIMEOUT,
    model="conformance-model",
    effort="conformance-effort",
    max_budget_usd=12.5,
    max_turns=97,
    max_ai_credits=13.75,
    fast=True,
)


def _declares(capabilities: Capabilities, option: str) -> bool:
    if option == "model":
        return capabilities.model
    if option == "effort":
        return capabilities.effort
    if option == "fast":
        return capabilities.fast
    return option in capabilities.budgets


def _reserved(agent: str) -> Mapping[str, Flag]:
    reserved: Mapping[str, Flag] = import_module(f"pratfall.adapters.{agent}")._RESERVED
    return reserved


def _marker(agent: str, marker: str | Mapping[str, str]) -> str | None:
    return marker if isinstance(marker, str) else marker.get(agent)


def _without(option: str) -> Options:
    if option == "model":
        return replace(_EVERY_CAPABILITY, model=None)
    if option == "effort":
        return replace(_EVERY_CAPABILITY, effort=None)
    if option == "max_budget_usd":
        return replace(_EVERY_CAPABILITY, max_budget_usd=None)
    if option == "max_turns":
        return replace(_EVERY_CAPABILITY, max_turns=None)
    if option == "max_ai_credits":
        return replace(_EVERY_CAPABILITY, max_ai_credits=None)
    return replace(_EVERY_CAPABILITY, fast=None)


def _added_positions(baseline: tuple[str, ...], variant: tuple[str, ...]) -> frozenset[int]:
    positions: set[int] = set()
    for tag, _, _, start, end in SequenceMatcher(
        a=baseline, b=variant, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            positions.update(range(start, end))
    return frozenset(positions)


def _owning_flag(variant: tuple[str, ...], index: int) -> str:
    argument = variant[index]
    if argument.startswith("-"):
        return argument.split("=", 1)[0]
    return variant[index - 1] if index else argument


_RESERVED_CASES: tuple[tuple[str, str], ...] = tuple(
    (agent, flag) for agent in sorted(ADAPTERS) for flag in _reserved(agent)
)


def test_adapter_requires_exactly_one_whole_document_or_consumer() -> None:
    with pytest.raises(TypeError, match="exactly one"):
        Adapter(build=claude.build, validate=claude.validate)
    with pytest.raises(TypeError, match="exactly one"):
        Adapter(
            build=codex.build,
            validate=codex.validate,
            whole_document=codex.decode,
            consumer=codex.consumer,
        )


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
@pytest.mark.parametrize(("option", "options", "marker"), _CAPABILITY_CASES)
def test_build_emits_exactly_the_capabilities_the_catalog_declares(
    agent: str, option: str, options: Options, marker: str | Mapping[str, str]
) -> None:
    build = ADAPTERS[agent].build
    baseline = build(resolved(agent, Options(timeout=_TIMEOUT)), b"prompt").argv
    variant = build(resolved(agent, options), b"prompt").argv
    if not _declares(BY_NAME[agent].capabilities, option):
        assert variant == baseline
        return
    assert variant != baseline
    text = _marker(agent, marker)
    assert text is not None
    added = _added_positions(baseline, variant)
    carriers = tuple(index for index, argument in enumerate(variant) if text in argument)
    assert carriers
    for index in carriers:
        assert index in added
        assert _owning_flag(variant, index) in _reserved(agent)


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
def test_build_ignores_undeclared_capabilities_when_every_option_is_set(agent: str) -> None:
    build = ADAPTERS[agent].build
    capabilities = BY_NAME[agent].capabilities
    everything = build(resolved(agent, _EVERY_CAPABILITY), b"prompt").argv
    for option, _, marker in _CAPABILITY_CASES:
        if _declares(capabilities, option):
            continue
        assert everything == build(resolved(agent, _without(option)), b"prompt").argv
        text = _marker(agent, marker)
        if text is not None:
            assert not any(text in argument for argument in everything)


@pytest.mark.parametrize(("agent", "flag"), _RESERVED_CASES)
def test_validate_rejects_owned_native_arguments(agent: str, flag: str) -> None:
    with pytest.raises(PratError, match="controlled by prat") as owned:
        ADAPTERS[agent].validate(resolved(agent, Options(native_args=(flag,))))
    assert owned.value.code == "invalid_arguments"


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
def test_validate_rejects_unknown_native_arguments(agent: str) -> None:
    probe = Options(native_args=("--prat-conformance-probe",))
    with pytest.raises(PratError, match="unknown native option") as unknown:
        ADAPTERS[agent].validate(resolved(agent, probe))
    assert unknown.value.code == "invalid_arguments"


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
    adapter: AdapterModule = claude if agent == "claude" else codex
    with pytest.raises(PratError, match="controlled by prat"):
        adapter.validate(resolved(agent, Options(native_args=arguments)))


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
    adapter: AdapterModule = claude if agent == "claude" else codex
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
    adapter: AdapterModule = claude if agent == "claude" else codex
    with pytest.raises(PratError, match=message):
        adapter.validate(resolved(agent, Options(native_args=arguments)))


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
    adapter: AdapterModule, agent: str, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(PratError, match="controlled by prat"):
        adapter.validate(resolved(agent, Options(native_args=arguments)))


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
    adapter: AdapterModule, agent: str, arguments: tuple[str, ...], message: str
) -> None:
    with pytest.raises(PratError, match=message):
        adapter.validate(resolved(agent, Options(native_args=arguments)))


@pytest.mark.parametrize("adapter", [kiro, hermes])
def test_text_adapters_preserve_stdout_and_remove_terminal_line_endings(
    adapter: AdapterModule,
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


_RETAINED_ANSWERS = ("retained", "retained-answer-" * 2, "retained-answer-" * 4)
_RETAINED_ANSWER = _RETAINED_ANSWERS[-1]
_RETENTION_STREAMS: Mapping[str, Callable[[str], str]] = {
    "amp": lambda answer: codex_stream(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ),
    "antigravity": lambda answer: codex_stream(
        {"event": "init", "init": {}},
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": answer,
                "usage": antigravity_usage(),
            },
        },
    ),
    "codex": lambda answer: codex_stream(
        {
            "type": "item.completed",
            "item": {"id": "item", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": codex_usage()},
    ),
    "copilot": lambda answer: codex_stream(
        {"type": "assistant.message", "data": {"messageId": "m", "content": answer}},
        copilot_result(),
    ),
    "kimi": lambda answer: codex_stream({"role": "assistant", "content": answer}),
    "opencode": lambda answer: codex_stream(
        {
            "type": "text",
            "part": {"id": "p", "type": "text", "text": answer, "time": {"end": 1}},
        },
        {
            "type": "step_finish",
            "part": {
                "id": "p",
                "type": "step-finish",
                "reason": "stop",
                "cost": 0,
                "tokens": opencode_usage(),
            },
        },
    ),
    "openhands": lambda answer: codex_stream(
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
            },
        }
    ),
    "qwen": lambda answer: codex_stream(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ),
    "warp": lambda answer: codex_stream({"type": "agent", "text": answer}),
}
_CONSUMER_AGENTS = sorted(
    name for name, adapter in ADAPTERS.items() if adapter.consumer is not None
)


def _consumer_factory(agent: str) -> ConsumerFactory:
    factory = ADAPTERS[agent].consumer
    assert factory is not None
    return factory


def _retention_stream(agent: str, answer: str) -> bytes:
    return _RETENTION_STREAMS[agent](answer).encode()


def _retains_within(agent: str, answer: str, state_bytes: int) -> bool:
    incremental = _consumer_factory(agent)(ConsumerLimits(state_bytes=state_bytes))
    try:
        incremental.feed(_retention_stream(agent, answer))
        incremental.finish()
    except ConsumerFailure:
        return False
    return True


def _minimum_state_bytes(agent: str, answer: str) -> int:
    low = 0
    high = ConsumerLimits().state_bytes
    assert _retains_within(agent, answer, high)
    while low < high:
        middle = (low + high) // 2
        if _retains_within(agent, answer, middle):
            high = middle
        else:
            low = middle + 1
    return low


def test_every_consumer_adapter_has_a_retained_answer_stream() -> None:
    assert sorted(_RETENTION_STREAMS) == _CONSUMER_AGENTS


@pytest.mark.parametrize("agent", _CONSUMER_AGENTS)
def test_retained_answer_stream_reaches_the_decoded_output(agent: str) -> None:
    decoded = decode_with(_consumer_factory(agent), _RETENTION_STREAMS[agent](_RETAINED_ANSWER))
    assert decoded.output == _RETAINED_ANSWER
    assert decoded.error is None


@pytest.mark.parametrize("agent", _CONSUMER_AGENTS)
def test_every_consumer_adapter_charges_its_retained_answer(agent: str) -> None:
    thresholds = [_minimum_state_bytes(agent, answer) for answer in _RETAINED_ANSWERS]
    overheads = [
        threshold - len(answer.encode())
        for threshold, answer in zip(thresholds, _RETAINED_ANSWERS, strict=True)
    ]
    assert overheads == [overheads[0]] * len(_RETAINED_ANSWERS)
    assert _retains_within(agent, _RETAINED_ANSWER, thresholds[-1])

    limits = ConsumerLimits(state_bytes=thresholds[-1] - 1)
    incremental = _consumer_factory(agent)(limits)
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(_retention_stream(agent, _RETAINED_ANSWER))
        incremental.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"


def test_progress_labels_cover_every_activity_category() -> None:
    assert set(ACTIVITY_LABELS) == set(get_args(Activity))


_UNUSED_FIELD = '"unused":null'
_WHOLE_JSON_DECODERS: dict[str, tuple[AdapterModule, str, str]] = {
    "claude": (
        claude,
        "Claude",
        '{"type":"result","subtype":"success","is_error":false,"result":"answer",'
        '"usage":{"input_tokens":1,"output_tokens":1},"unused":null}',
    ),
    "cursor": (
        cursor,
        "Cursor",
        '{"type":"result","subtype":"success","is_error":false,"result":"answer","unused":null}',
    ),
    "droid": (
        droid,
        "Droid",
        '{"type":"result","subtype":"success","is_error":false,"result":"answer","unused":null}',
    ),
    "gemini": (gemini, "Gemini", '{"response":"answer","unused":null}'),
    "openclaw": (
        openclaw,
        "OpenClaw",
        '{"ok":true,"status":"ok","final":"answer","payloads":[],"unused":null}',
    ),
    "reasonix": (
        reasonix,
        "Reasonix",
        '{"type":"result","subtype":"success","is_error":false,"result":"answer","unused":null}',
    ),
    "vibe": (
        vibe,
        "Vibe",
        '[{"type":"message","role":"assistant",'
        '"content":[{"type":"text","text":"answer"}],"unused":null}]',
    ),
}
_WHOLE_JSON_DOCUMENT_GUARDS: tuple[tuple[str, Callable[[], str], Code, str], ...] = (
    ("framing", lambda: "", "protocol_error", "Invalid {label} JSON: Expecting value."),
    (
        "nesting",
        lambda: "[" * 100_000 + "]" * 100_000,
        "protocol_error",
        "Invalid {label} JSON: document nesting is too deep.",
    ),
    (
        "oversized-document",
        lambda: "a" * (STDOUT_BYTES + 1),
        "stdout_limit_exceeded",
        "{label} JSON exceeds 8 MiB.",
    ),
)
_WHOLE_JSON_FIELD_GUARDS: tuple[tuple[str, str, str, Code, str], ...] = (
    (
        "duplicate-key",
        '"unused":1,"unused":2',
        "",
        "protocol_error",
        "Invalid {label} JSON: duplicate object key.",
    ),
    (
        "nonfinite-number",
        '"unused":NaN',
        "answer",
        "protocol_error",
        "{label} JSON contains a nonfinite number.",
    ),
    (
        "infinite-number",
        '"unused":Infinity',
        "answer",
        "protocol_error",
        "{label} JSON contains a nonfinite number.",
    ),
    (
        "overflowing-exponent",
        '"unused":1e400',
        "answer",
        "protocol_error",
        "{label} JSON contains a nonfinite number.",
    ),
    (
        "wide-number",
        '"unused":' + "9" * 200,
        "",
        "protocol_error",
        "Invalid {label} JSON: numeric value is too large.",
    ),
    (
        "huge-number",
        '"unused":' + "9" * 5000,
        "",
        "protocol_error",
        "Invalid {label} JSON: numeric value is too large.",
    ),
    (
        "lone-surrogate",
        '"unused":"\\ud800"',
        "answer",
        "output_encoding",
        "Agent output contains invalid Unicode text.",
    ),
)


def _whole_json_document(agent: str, extra: str) -> str:
    return _WHOLE_JSON_DECODERS[agent][2].replace(_UNUSED_FIELD, extra)


@pytest.mark.parametrize("agent", sorted(_WHOLE_JSON_DECODERS))
def test_every_whole_document_decoder_accepts_its_baseline_document(agent: str) -> None:
    decoded = _WHOLE_JSON_DECODERS[agent][0].decode(_WHOLE_JSON_DECODERS[agent][2])
    assert decoded.output == "answer"
    assert decoded.error is None


@pytest.mark.parametrize("agent", sorted(_WHOLE_JSON_DECODERS))
@pytest.mark.parametrize(
    ("document", "code", "message"),
    [case[1:] for case in _WHOLE_JSON_DOCUMENT_GUARDS],
    ids=[case[0] for case in _WHOLE_JSON_DOCUMENT_GUARDS],
)
def test_every_whole_document_decoder_reports_one_document_guard(
    agent: str, document: Callable[[], str], code: Code, message: str
) -> None:
    module, label, _ = _WHOLE_JSON_DECODERS[agent]
    decoded = module.decode(document())
    assert decoded.output == ""
    assert decoded.error == ResultError(code, message.format(label=label))


@pytest.mark.parametrize("agent", sorted(_WHOLE_JSON_DECODERS))
@pytest.mark.parametrize(
    ("extra", "output", "code", "message"),
    [case[1:] for case in _WHOLE_JSON_FIELD_GUARDS],
    ids=[case[0] for case in _WHOLE_JSON_FIELD_GUARDS],
)
def test_every_whole_document_decoder_rejects_a_guarded_ignored_field(
    agent: str, extra: str, output: str, code: Code, message: str
) -> None:
    module, label, _ = _WHOLE_JSON_DECODERS[agent]
    decoded = module.decode(_whole_json_document(agent, extra))
    assert decoded.output == output
    assert decoded.error == ResultError(code, message.format(label=label))


@pytest.mark.parametrize("agent", sorted(_WHOLE_JSON_DECODERS))
def test_every_whole_document_decoder_clears_an_unencodable_answer(agent: str) -> None:
    module, _, document = _WHOLE_JSON_DECODERS[agent]
    decoded = module.decode(document.replace('"answer"', json.dumps("a\ud800")))
    assert decoded.output == ""
    assert decoded.error == ResultError(
        "output_encoding", "Agent output contains invalid Unicode text."
    )
