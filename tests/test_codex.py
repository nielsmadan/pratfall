import json
from dataclasses import replace

import pytest

from adapter_helpers import codex_stream, codex_usage, resolved
from pratfall.adapters import codex
from pratfall.adapters.registry import ADAPTERS
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, PreparedSchema, ResultError, Usage


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
    assert decoded.error is not None
    assert decoded.error.code == "provider_error"
    assert decoded.error.message == "API unavailable"


def schema_message(text: str, identifier: str = "answer") -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {"id": identifier, "type": "agent_message", "text": text},
    }


def test_codex_schema_build_and_native_collision() -> None:
    profile = replace(
        resolved("codex", Options(schema="source", attachments=("/image.png",))),
        prepared_schema=PreparedSchema('{"type":"object"}', {"type": "object"}, "/snapshot"),
    )
    assert codex.build(profile, b"prompt").argv == (
        "codex-wrapper",
        "native",
        "exec",
        "--json",
        "--image=/image.png",
        "--output-schema",
        "/snapshot",
        "--",
        "-",
    )
    for arguments in (("--output-schema", "native"), ("--output-schema=native",)):
        native = resolved("codex", Options(native_args=arguments))
        codex.validate(native)
        with pytest.raises(PratError, match="controlled by prat"):
            codex.validate(replace(native, options=replace(native.options, schema="source")))


@pytest.mark.parametrize("value", [None, False, 0, "", "雪", [], {}, {"answer": [1, True]}])
@pytest.mark.parametrize("chunk", [1, 11, 65536])
def test_codex_schema_final_message_parity(value: object, chunk: int) -> None:
    text = codex_stream(
        {"type": "turn.started"},
        schema_message("Planning prose"),
        schema_message(json.dumps(value), "final"),
        {"type": "turn.completed", "usage": codex_usage()},
    )
    bound = ADAPTERS["codex"].for_schema(True)
    consumer = codex.schema_consumer()
    encoded = text.encode()
    for start in range(0, len(encoded), chunk):
        consumer.feed(encoded[start : start + chunk])
    decoded = consumer.finish()
    assert decoded == codex.decode_schema(text) == bound.decode(text)
    assert decoded.error is None
    assert decoded.structured_output_present
    assert decoded.structured_output == value
    assert json.loads(decoded.output) == value
    assert decoded.usage == Usage(24_901, 8_960, 0, 8, 0)


def test_codex_schema_selects_last_turn_without_joining() -> None:
    text = codex_stream(
        schema_message('{"first":1}'),
        {"type": "turn.completed", "usage": codex_usage()},
        {"type": "turn.started"},
        schema_message("[2]"),
        {"type": "turn.completed", "usage": codex_usage(output_tokens=9)},
    )
    decoded = codex.decode_schema(text)
    assert decoded.output == "[2]"
    assert decoded.error is None
    assert decoded.usage is not None and decoded.usage.output_tokens == 9
    assert codex.decode(text).output == '{"first":1}\n[2]'


@pytest.mark.parametrize(
    "last", ["prose", "", '{"a":1,"a":2}', "NaN", "1e999", "1" * 129, "[" * 65 + "0" + "]" * 65]
)
def test_codex_schema_bad_final_keeps_safe_partial(last: str) -> None:
    decoded = codex.decode_schema(
        codex_stream(
            schema_message('{"partial":true}'),
            schema_message(last),
            {"type": "turn.completed", "usage": codex_usage()},
        )
    )
    assert decoded.output == '{"partial":true}'
    assert decoded.structured_output == {"partial": True}
    assert decoded.error is not None and decoded.error.code == "protocol_error"
    assert decoded.usage is not None


@pytest.mark.parametrize(
    "ending",
    [
        [],
        [{"type": "turn.started"}],
        [{"type": "turn.started"}, {"type": "turn.completed", "usage": codex_usage()}],
        [{"type": "turn.failed", "error": {"message": "denied"}}],
    ],
)
def test_codex_schema_completion_and_failure(ending: list[dict[str, object]]) -> None:
    decoded = codex.decode_schema(codex_stream(schema_message("null"), *ending))
    assert decoded.output == "null" and decoded.structured_output_present
    assert decoded.error is not None
    assert decoded.error.code == (
        "provider_error" if ending and ending[-1]["type"] == "turn.failed" else "protocol_error"
    )


def test_codex_schema_missing_message() -> None:
    decoded = codex.decode_schema(codex_stream({"type": "turn.completed", "usage": codex_usage()}))
    assert decoded.output == "" and not decoded.structured_output_present
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_codex_schema_replacement_refunds_and_charges_both_representations() -> None:
    consumer = codex.schema_consumer(ConsumerLimits(state_bytes=8, records=1))
    for _ in range(20):
        consumer.feed(codex_stream(schema_message("null")).encode())
    with pytest.raises(ConsumerFailure, match="retained output state"):
        consumer.feed(codex_stream(schema_message('"too large"')).encode())
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.decoded is not None
    assert failure.value.decoded.output == "null"
    assert failure.value.decoded.structured_output_present


@pytest.mark.parametrize(
    "record",
    [
        '{"type":"item.completed","item":{"id":"x","type":"agent_message","text":"null","text":"0"}}',
        '{"type":"future","payload":NaN}',
        '{"type":"future","payload":' + "[" * 65 + "0" + "]" * 65 + "}",
    ],
)
def test_codex_schema_strict_event_json(record: str) -> None:
    decoded = codex.decode_schema(record)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_codex_schema_invalid_unicode_answer() -> None:
    decoded = codex.decode_schema(codex_stream(schema_message('"\\ud800"')))
    assert decoded.error is not None and decoded.error.code == "output_encoding"


_OWNED_CONFIG = (
    ("developer_instructions", Options(instructions="native guidance")),
    ("developer_instructions", Options(instructions_file="instructions.md")),
    ("model", Options(model="gpt-5.6-luna")),
    ("model_reasoning_effort", Options(effort="high")),
    ("service_tier", Options(fast=True)),
)


@pytest.mark.parametrize(
    "arguments",
    [
        ("-c", "shell_environment_policy.inherit=all"),
        ("-cshell_environment_policy.inherit=all",),
        ("--config", "sandbox_workspace_write.network_access=true"),
        ("--config=sandbox_workspace_write.network_access=true",),
    ],
)
def test_codex_accepts_unowned_config_overrides(arguments: tuple[str, ...]) -> None:
    codex.validate(resolved("codex", Options(effort="high", native_args=arguments)))


@pytest.mark.parametrize("key", [key for key, _ in _OWNED_CONFIG])
def test_codex_allows_owned_config_key_without_the_public_option(key: str) -> None:
    codex.validate(resolved("codex", Options(native_args=("-c", f"{key}=1"))))


@pytest.mark.parametrize(("key", "options"), _OWNED_CONFIG)
@pytest.mark.parametrize("form", ["-c {}=1", "-c{}=1", "-c={}=1", "--config {}=1", "--config={}=1"])
def test_codex_rejects_owned_config_key_when_the_public_option_is_active(
    key: str, options: Options, form: str
) -> None:
    arguments = tuple(form.format(key).split(" "))
    with pytest.raises(PratError, match="controlled by prat"):
        codex.validate(resolved("codex", replace(options, native_args=arguments)))


@pytest.mark.parametrize(("key", "options"), _OWNED_CONFIG)
@pytest.mark.parametrize("spacing", ["{} =1", " {}=1", "{}\t= 1"])
def test_codex_rejects_owned_config_key_around_native_whitespace(
    key: str, options: Options, spacing: str
) -> None:
    arguments = ("-c", spacing.format(key))
    with pytest.raises(PratError, match="controlled by prat"):
        codex.validate(resolved("codex", replace(options, native_args=arguments)))


@pytest.mark.parametrize(("key", "options"), _OWNED_CONFIG)
def test_codex_rejects_owned_config_descendant_when_the_public_option_is_active(
    key: str, options: Options
) -> None:
    with pytest.raises(PratError, match="controlled by prat"):
        codex.validate(resolved("codex", replace(options, native_args=("-c", f"{key}.nested=1"))))


@pytest.mark.parametrize(("key", "options"), _OWNED_CONFIG)
def test_codex_rejects_owned_config_key_without_a_value_separator(
    key: str, options: Options
) -> None:
    with pytest.raises(PratError, match="controlled by prat"):
        codex.validate(resolved("codex", replace(options, native_args=("-c", key))))


def test_codex_config_override_reports_the_native_argument() -> None:
    with pytest.raises(PratError, match=r"'-c model_reasoning_effort=\"low\"'"):
        codex.validate(
            resolved(
                "codex",
                Options(effort="high", native_args=("-c", 'model_reasoning_effort="low"')),
            )
        )


def test_codex_reports_the_native_thread_id() -> None:
    decoded = codex.decode(
        codex_stream(
            {"type": "thread.started", "thread_id": "codex-thread"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "agent_message", "text": "answer"},
            },
            {"type": "turn.completed", "usage": codex_usage()},
        )
    )
    assert decoded.session_id == "codex-thread"
    assert decoded.output == "answer"


def test_codex_reports_the_thread_on_its_failure_paths() -> None:
    """The session is worth most on a run that went wrong: a successful run already
    printed its answer."""
    started = {"type": "thread.started", "thread_id": "codex-thread"}
    truncated = codex.decode(codex_stream(started, {"type": "turn.started"}))
    assert truncated.error is not None
    assert truncated.session_id == "codex-thread"

    failed = codex.decode(
        codex_stream(
            started,
            {"type": "turn.started"},
            {"type": "turn.failed", "error": {"message": "upstream exploded"}},
        )
    )
    assert failed.error is not None
    assert failed.session_id == "codex-thread"
