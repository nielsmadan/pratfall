import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import resolved as resolved_profile
from pratfall.adapters import amp
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, Usage


def stream(*events: object) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def assistant(**fields: object) -> dict[str, object]:
    return {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"content": [{"type": "text", "text": "partial"}]},
        **fields,
    }


def result(**fields: object) -> dict[str, object]:
    return {"type": "result", "subtype": "success", "is_error": False, "result": "final", **fields}


def test_amp_literal_stdin_and_native_thinking() -> None:
    resolved = ResolvedProfile(
        BY_NAME["amp"], None, ("wrapper", "$HOME"), Options(native_args=("--stream-json-thinking",))
    )
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = amp.build(resolved, prompt)
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--execute",
        "--stream-json",
        "--stream-json-thinking",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--execute",),
        ("-xx",),
        ("--stream-json",),
        ("--stream-json-input",),
        ("--output-format=json",),
        ("--model=x",),
        ("--effort=high",),
        ("--mode=smart",),
        ("--executor=orb",),
        ("-ox",),
        ("--runner-id=x",),
        ("--cwd=x",),
        ("--config=x",),
        ("--settings-file=x",),
        ("--thread=x",),
        ("--resume=x",),
        ("--no-tui",),
        ("threads", "continue"),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--stream-json-thinking=true",),
    ],
)
def test_amp_reserved_and_unknown_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        amp.validate(resolved_profile("amp", Options(native_args=arguments)))


@pytest.mark.parametrize(
    "options",
    [
        Options(model="model"),
        Options(effort="high"),
        Options(fast=False),
        Options(max_turns=3),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_amp_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "amp", options)


def test_amp_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            "amp",
            "--json",
            f"--prompt={prompt}",
            "--",
            "--stream-json-thinking",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    native = json.loads(payload["output"])
    assert status == payload["exit_code"] == payload["native_exit_code"] == 0
    assert native["argv"] == ["--execute", "--stream-json", "--stream-json-thinking"]
    assert native["stdin"] == native["prompt"] == prompt
    assert payload["reported_models"] is payload["cost_usd"] is None
    assert payload["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "cached_input_tokens": 1,
        "cache_write_input_tokens": 4,
        "reasoning_output_tokens": None,
    }


def test_amp_final_accounting_and_chunked_unicode() -> None:
    payload = stream(
        {"type": "system", "subtype": "init", "agent_mode": "smart"},
        assistant(
            message={
                "content": [{"type": "text", "text": "prior"}],
                "usage": {"input_tokens": 999, "output_tokens": 999},
            }
        ),
        assistant(
            parent_tool_use_id="child", message={"content": [{"type": "text", "text": "child"}]}
        ),
        result(
            result="café 雪",
            usage={
                "input_tokens": 0,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            },
        ),
    )
    incremental = amp.consumer()
    for byte in payload.rstrip("\n").encode():
        incremental.feed(bytes([byte]))
    decoded = incremental.finish()
    assert decoded.output == "café 雪" and decoded.error is None
    assert decoded.usage == Usage(0, 3, 4, 2) and decoded.reported_models is None


def test_amp_no_final_usage_means_unknown_even_with_message_snapshot() -> None:
    message = {
        "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "redacted_thinking", "data": "private"},
            {"type": "tool_use", "name": "Read"},
            {"type": "text", "text": "one"},
            {"type": "text", "text": "two"},
        ],
        "usage": {"input_tokens": 999, "output_tokens": 999},
    }
    decoded = amp.decode(stream(assistant(message=message), result(result="onetwo")))
    assert decoded.output == "onetwo" and decoded.usage is None and decoded.error is None


@pytest.mark.parametrize("event_type", ["system", "result"])
@pytest.mark.parametrize("subtype", ["error_during_execution", "error_max_turns"])
def test_amp_error_strings_retain_root_text(event_type: str, subtype: str) -> None:
    decoded = amp.decode(
        stream(
            assistant(),
            {"type": event_type, "subtype": subtype, "is_error": True, "error": "native failure"},
        )
    )
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "native failure"


@pytest.mark.parametrize(
    "suffix",
    [
        "garbage",
        "[]",
        "{}",
        stream({"type": "future"}),
        stream(result(subtype="future")),
        stream(
            result(
                subtype="error_during_execution", is_error=True, error={"message": "wrong schema"}
            )
        ),
        stream(result(result=[])),
        stream(result(error="contradiction")),
        stream(assistant(parent_tool_use_id=3)),
        stream(assistant(message=[])),
        stream(assistant(message={"content": [{"type": []}]})),
        stream(assistant(message={"content": [{"type": "text", "text": []}]})),
        stream({"type": "system", "subtype": "unknown"}),
        "[" * 2000 + "]" * 2000,
        '{"type":"system","n":' + "1" * 129 + "}",
    ],
)
def test_amp_malformed_records_keep_partial_text(suffix: str) -> None:
    decoded = amp.decode(stream(assistant()) + suffix)
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "usage",
    [
        [],
        {},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": 1, "output_tokens": -1},
        {"input_tokens": 1, "output_tokens": float("nan")},
        {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": None},
    ],
)
def test_amp_invalid_final_usage_preserves_answer(usage: object) -> None:
    decoded = amp.decode(stream(result(usage=usage)))
    assert decoded.output == "final" and decoded.usage is None
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload", ["", stream(assistant()), stream(result(), result()), stream(result(), assistant())]
)
def test_amp_requires_one_terminal_result(payload: str) -> None:
    decoded = amp.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("limits", [ConsumerLimits(state_bytes=10), ConsumerLimits(records=1)])
def test_amp_bounds_retained_state(limits: ConsumerLimits) -> None:
    incremental = amp.consumer(limits)
    incremental.feed(stream(assistant()).encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(stream(result(result="final answer")).encode())
    assert failure.value.error.code == "stdout_limit_exceeded"
    with pytest.raises(ConsumerFailure) as final:
        incremental.finish()
    assert final.value.decoded is not None
    assert final.value.decoded.output == ("partial" if limits.state_bytes == 10 else "final answer")


def test_amp_unicode_failure_retains_answer() -> None:
    decoded = amp.decode(stream(assistant(), result(result="\ud800")))
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "output_encoding"


@pytest.mark.parametrize(
    ("text", "error_code"),
    [(None, "protocol_error"), ([], "protocol_error"), ("\ud800", "output_encoding")],
)
def test_amp_invalid_terminal_text_retains_final_accounting(text: object, error_code: str) -> None:
    decoded = amp.decode(
        stream(
            assistant(),
            result(
                result=text,
                usage={
                    "input_tokens": 13,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 5,
                    "cache_creation_input_tokens": 3,
                },
            ),
        )
    )
    assert decoded.output == "partial"
    assert decoded.usage == Usage(13, 5, 3, 7)
    assert decoded.error is not None and decoded.error.code == error_code


def test_amp_malformed_text_replaces_accounting_with_bounded_state() -> None:
    incremental = amp.consumer(ConsumerLimits(state_bytes=64, records=2))
    incremental.feed(stream(assistant()).encode())
    for count in range(100):
        incremental.feed(
            stream(result(result=None, usage={"input_tokens": count, "output_tokens": 7})).encode()
        )
    decoded = incremental.finish()
    assert decoded.output == "partial" and decoded.usage == Usage(input_tokens=99, output_tokens=7)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("native_exit", [0, 17])
def test_amp_runner_normalizes_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], native_exit: int
) -> None:
    payload = stream(
        assistant(), result(subtype="error_during_execution", is_error=True, error="failed")
    )
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.amp]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "amp", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or 1)
    assert value["native_exit_code"] == native_exit and value["output"] == "partial"
    assert value["error"]["code"] == ("native_exit" if native_exit else "provider_error")


def test_amp_native_failure_outranks_malformed_optional_usage() -> None:
    decoded = amp.decode(
        stream(
            assistant(),
            result(
                subtype="error_during_execution", is_error=True, error="native failed", usage=[]
            ),
        )
    )
    assert decoded.output == "partial" and decoded.usage is None
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "native failed"
