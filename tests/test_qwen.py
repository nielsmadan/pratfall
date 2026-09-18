import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from adapter_helpers import resolved as resolved_profile
from pratfall.adapters import qwen
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, PreparedSchema, ResolvedProfile, Usage
from pratfall.schema import parse_json


def stream(*events: object) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def assistant(text: str = "partial", **fields: object) -> dict[str, object]:
    return {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"content": [{"type": "text", "text": text}], "model": "root-model"},
        **fields,
    }


def result(**fields: object) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "final",
        "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 1},
        **fields,
    }


def test_qwen_literal_stdin_and_controls() -> None:
    options = Options(
        model="configured", max_turns=7, native_args=("--debug", "--approval-mode=plan")
    )
    resolved = ResolvedProfile(BY_NAME["qwen"], None, ("wrapper", "$HOME"), options)
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = qwen.build(resolved, prompt)
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--output-format",
        "stream-json",
        "--model",
        "configured",
        "--max-session-turns",
        "7",
        "--debug",
        "--approval-mode=plan",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=x",),
        ("-px",),
        ("--input-format=stream-json",),
        ("--output-format=json",),
        ("-otext",),
        ("--model=x",),
        ("-mx",),
        ("--max-session-turns=2",),
        ("--max-wall-time=3",),
        ("--max-tool-calls=3",),
        ("--continue",),
        ("--resume=x",),
        ("-rx",),
        ("--session-id=x",),
        ("--worktree=x",),
        ("--cwd=x",),
        ("--config=x",),
        ("--fallback-model=x",),
        ("--acp",),
        ("--effort=high",),
        ("serve",),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--debug=true",),
        ("--approval-mode",),
        ("--json-schema={}",),
    ],
)
def test_qwen_reserved_and_unknown_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        qwen.validate(resolved_profile("qwen", Options(native_args=arguments)))


@pytest.mark.parametrize(
    "options",
    [
        Options(effort="high"),
        Options(fast=False),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_qwen_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "qwen", options)


def test_qwen_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            "qwen",
            "--json",
            "--model",
            "model-x",
            "--max-turns",
            "4",
            f"--prompt={prompt}",
            "--",
            "--approval-mode=plan",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    native = json.loads(payload["output"])
    assert status == payload["exit_code"] == payload["native_exit_code"] == 0
    assert native["argv"] == [
        "--output-format",
        "stream-json",
        "--model",
        "model-x",
        "--max-session-turns",
        "4",
        "--approval-mode=plan",
    ]
    assert native["stdin"] == native["prompt"] == prompt
    assert payload["reported_models"] == ["qwen-native"] and payload["cost_usd"] is None
    assert payload["usage"]["input_tokens"] == 3


def test_qwen_last_result_recovers_subagent_failure_and_uses_final_usage() -> None:
    partial = assistant()
    message = partial["message"]
    assert isinstance(message, dict)
    message["usage"] = {"input_tokens": 900, "output_tokens": 700}
    payload = stream(
        partial,
        result(subtype="error_during_execution", is_error=True, error={"message": "child failed"}),
        assistant(
            "child",
            parent_tool_use_id="tool-1",
            message={"content": [{"type": "text", "text": "child"}], "model": "child-model"},
        ),
        {"type": "user", "message": {"role": "user", "content": []}},
        {"type": "system", "subtype": "task_notification"},
        result(result="final café 雪"),
    )
    incremental = qwen.consumer()
    for byte in payload.rstrip("\n").encode():
        incremental.feed(bytes([byte]))
    decoded = incremental.finish()
    assert decoded.output == "final café 雪" and decoded.error is None
    assert decoded.reported_models == ("root-model",) and decoded.cost_usd is None
    assert decoded.usage == Usage(input_tokens=3, output_tokens=2, cached_input_tokens=1)


def test_qwen_distinct_root_models_and_thinking_tool_exclusion() -> None:
    message = {
        "model": "second",
        "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "tool_use", "name": "Read"},
            {"type": "text", "text": "one"},
            {"type": "text", "text": "two"},
        ],
    }
    decoded = qwen.decode(
        stream(assistant(), assistant(message=message), assistant(message=message))
    )
    assert decoded.output == "onetwo" and decoded.reported_models == ("root-model", "second")
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("subtype", ["error_during_execution", "error_max_turns"])
def test_qwen_final_failure_retains_partial_text_and_usage(subtype: str) -> None:
    decoded = qwen.decode(
        stream(
            assistant(), result(subtype=subtype, is_error=True, error={"message": "limit reached"})
        )
    )
    assert decoded.output == "partial" and decoded.usage == Usage(3, 1, None, 2)
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "limit reached"


@pytest.mark.parametrize(
    "suffix",
    [
        "garbage",
        "[]",
        "{}",
        stream({"type": "future"}),
        stream(result(subtype="future")),
        stream(result(subtype="error_during_execution", is_error=True, error="wrong schema")),
        stream(result(result=[])),
        stream(result(error={"message": "contradiction"})),
        stream(assistant(parent_tool_use_id=3)),
        stream(assistant(message=[])),
        stream(assistant(message={"content": [{"type": []}]})),
        stream(assistant(message={"content": [{"type": "text", "text": []}]})),
        stream(assistant(message={"model": [], "content": []})),
        stream({"type": "system"}),
        stream({"type": "stream_event", "event": []}),
        "[" * 2000 + "]" * 2000,
        '{"type":"system","n":' + "1" * 129 + "}",
    ],
)
def test_qwen_malformed_records_keep_prior_text(suffix: str) -> None:
    decoded = qwen.decode(stream(assistant()) + suffix)
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("payload", ["", stream(assistant()), stream(result(), assistant())])
def test_qwen_requires_result_at_completion(payload: str) -> None:
    decoded = qwen.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_qwen_replacement_refunds_result_and_text_budgets() -> None:
    incremental = qwen.consumer(ConsumerLimits(state_bytes=20, records=2))
    for _ in range(100):
        incremental.feed(stream(result(result="small", usage=None)).encode())
    decoded = incremental.finish()
    assert decoded.output == "small" and decoded.error is None and decoded.usage is None


@pytest.mark.parametrize("limits", [ConsumerLimits(state_bytes=17), ConsumerLimits(records=1)])
def test_qwen_bounds_retained_text_and_models(limits: ConsumerLimits) -> None:
    incremental = qwen.consumer(limits)
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(stream(assistant("too much retained output")).encode())
    assert failure.value.error.code == "stdout_limit_exceeded"
    with pytest.raises(ConsumerFailure) as final:
        incremental.finish()
    assert final.value.decoded is not None and final.value.decoded.reported_models == (
        "root-model",
    )


def test_qwen_unicode_failure_retains_prior_answer() -> None:
    decoded = qwen.decode(stream(assistant(), result(result="\ud800")))
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "output_encoding"


@pytest.mark.parametrize(
    ("text", "error_code"),
    [(None, "protocol_error"), ([], "protocol_error"), ("\ud800", "output_encoding")],
)
def test_qwen_invalid_terminal_text_retains_final_accounting(text: object, error_code: str) -> None:
    decoded = qwen.decode(
        stream(
            assistant(),
            result(
                result=text,
                usage={"input_tokens": 13, "output_tokens": 7, "cache_read_input_tokens": 5},
            ),
        )
    )
    assert decoded.output == "partial" and decoded.reported_models == ("root-model",)
    assert decoded.usage == Usage(13, 5, None, 7)
    assert decoded.error is not None and decoded.error.code == error_code


@pytest.mark.parametrize("native_exit", [0, 17])
def test_qwen_runner_normalizes_terminal_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], native_exit: int
) -> None:
    payload = stream(
        assistant(),
        result(subtype="error_during_execution", is_error=True, error={"message": "failed"}),
    )
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.qwen]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "qwen", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or 1)
    assert value["native_exit_code"] == native_exit and value["output"] == "partial"
    assert value["error"]["code"] == ("native_exit" if native_exit else "provider_error")


def test_qwen_native_failure_outranks_malformed_optional_usage() -> None:
    decoded = qwen.decode(
        stream(
            assistant(),
            result(
                subtype="error_during_execution",
                is_error=True,
                error={"message": "native failed"},
                usage=[],
            ),
        )
    )
    assert decoded.output == "partial" and decoded.usage is None
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "native failed"


@pytest.mark.parametrize(
    "usage",
    [
        [],
        {},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": float("inf"), "output_tokens": 1},
        {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": None},
    ],
)
def test_qwen_invalid_final_usage_preserves_answer(usage: object) -> None:
    decoded = qwen.decode(stream(result(usage=usage)))
    assert decoded.output == "final" and decoded.usage is None
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("value", [None, False, 0, "", "雪", [], {}, {"answer": [1, True]}])
@pytest.mark.parametrize("chunk", [1, 13, 65536])
def test_qwen_schema_terminal_result_parity(value: object, chunk: int) -> None:
    text = stream(
        assistant("interim"),
        assistant("child", parent_tool_use_id="tool"),
        result(structured_result=value, result="not authoritative", structured_output="wrong"),
    )
    consumer = qwen.schema_consumer()
    encoded = text.encode()
    for start in range(0, len(encoded), chunk):
        consumer.feed(encoded[start : start + chunk])
    decoded = consumer.finish()
    assert decoded == qwen.decode_schema(text) == ADAPTERS["qwen"].for_schema(True).decode(text)
    assert decoded.error is None
    assert decoded.structured_output_present and decoded.structured_output == value
    assert json.loads(decoded.output) == value
    assert decoded.reported_models == ("root-model",)
    assert decoded.usage == Usage(input_tokens=3, output_tokens=2, cached_input_tokens=1)


@pytest.mark.parametrize(
    "ending",
    [
        result(),
        result(structured_output={"wrong": True}),
        result(subtype="error_during_execution", is_error=True, error={"message": "denied"}),
        assistant("later"),
    ],
)
def test_qwen_schema_later_failure_keeps_safe_partial(ending: dict[str, object]) -> None:
    decoded = qwen.decode_schema(stream(result(structured_result={"safe": True}), ending))
    assert decoded.output == '{"safe":true}'
    assert decoded.error is not None
    assert decoded.error.code == ("provider_error" if ending.get("is_error") else "protocol_error")


def test_qwen_schema_ignores_child_and_tool_answers() -> None:
    decoded = qwen.decode_schema(
        stream(
            assistant(
                message={
                    "model": "root",
                    "content": [
                        {"type": "tool_use", "name": "structured_output", "input": {"wrong": 1}},
                    ],
                }
            ),
            result(structured_result={"wrong": 2}, parent_tool_use_id="child"),
        )
    )
    assert decoded.output == "" and not decoded.structured_output_present
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_qwen_schema_recovers_from_intermediate_subagent_error() -> None:
    decoded = qwen.decode_schema(
        stream(
            result(subtype="error_during_execution", is_error=True, error={"message": "child"}),
            assistant(),
            result(structured_result={}),
        )
    )
    assert decoded.error is None and decoded.output == "{}"


def test_qwen_schema_replacement_budget_and_failed_replacement() -> None:
    consumer = qwen.schema_consumer(ConsumerLimits(state_bytes=8, records=2))
    for _ in range(20):
        consumer.feed(stream(result(structured_result={}, usage=None)).encode())
    consumer.feed(stream(result(structured_result=None, usage=None)).encode())
    with pytest.raises(ConsumerFailure, match="retained output state"):
        consumer.feed(stream(result(structured_result="large", usage=None)).encode())
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.decoded is not None
    assert failure.value.decoded.output == "null"


@pytest.mark.parametrize(
    "payload,code",
    [
        ('{"a":1,"a":2}', "protocol_error"),
        ("NaN", "protocol_error"),
        ("1e999", "protocol_error"),
        ("1" * 129, "protocol_error"),
        ('"\\ud800"', "output_encoding"),
        ("[" * 65 + "0" + "]" * 65, "protocol_error"),
    ],
)
def test_qwen_schema_strict_terminal_value(payload: str, code: str) -> None:
    text = (
        '{"type":"result","subtype":"success","is_error":false,"structured_result":' + payload + "}"
    )
    decoded = qwen.decode_schema(text)
    assert decoded.error is not None and decoded.error.code == code
    assert not decoded.structured_output_present


@pytest.mark.parametrize(
    "document",
    [
        False,
        {"type": "string"},
        {"type": ["array", "null"]},
        {"const": 1},
        {"enum": [1, None]},
        {"anyOf": [{"type": "string"}, {"type": "number"}]},
        {"allOf": [{"type": "object"}, {"type": "string"}]},
        {"oneOf": [False]},
        {"$ref": "#/$defs/object"},
    ],
)
def test_qwen_schema_rejects_known_unusable_roots(document: object) -> None:
    with pytest.raises(PratError):
        qwen.validate_schema(PreparedSchema(json.dumps(document), parse_json(json.dumps(document))))


@pytest.mark.parametrize(
    "document",
    [
        True,
        {},
        {"type": "object"},
        {"type": ["object", "null"]},
        {"const": {}},
        {"enum": [None, {}]},
        {"anyOf": [{"type": "string"}, {"type": "object"}]},
        {"allOf": [{"$ref": "#/$defs/object"}]},
        {"not": {"type": "object"}},
    ],
)
def test_qwen_schema_defers_uncertain_dialect_validation(document: object) -> None:
    qwen.validate_schema(PreparedSchema(json.dumps(document), parse_json(json.dumps(document))))


def test_qwen_schema_transport_preserves_explicit_tool_denial() -> None:
    profile = replace(
        resolved_profile(
            "qwen",
            Options(schema="source", tools=("read_file",), disabled_tools=("structured_output",)),
        ),
        prepared_schema=PreparedSchema("{}", {}, "/private/schema.json"),
    )
    qwen.validate(profile)
    invocation = qwen.build(profile, b"task")
    assert invocation.argv == (
        "qwen-wrapper",
        "native",
        "--output-format",
        "stream-json",
        "--core-tools=read_file",
        "--exclude-tools=structured_output",
        "--json-schema",
        "@/private/schema.json",
    )
    assert invocation.stdin == b"task"
