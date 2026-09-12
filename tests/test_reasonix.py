import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import reasonix
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, Usage


def result(**fields: object) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "answer",
        "usage": {
            "input_tokens": 3,
            "output_tokens": 2,
            "cache_read_input_tokens": 1,
            "cache_creation_input_tokens": 7,
            "estimated": True,
        },
        **fields,
    }


def test_reasonix_literal_stdin_and_native_controls() -> None:
    options = Options(
        model="configured-provider", effort="high", native_args=("--max-steps=8", "--show-thinking")
    )
    resolved = ResolvedProfile(BY_NAME["reasonix"], None, ("wrapper", "$HOME"), options)
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = reasonix.build(resolved, prompt)
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "run",
        "--output-format",
        "json",
        "--model",
        "configured-provider",
        "--effort",
        "high",
        "--max-steps=8",
        "--show-thinking",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=x",),
        ("--print",),
        ("-p",),
        ("--output-format=text",),
        ("--events-jsonl",),
        ("--model=x",),
        ("--effort=high",),
        ("--dir=x",),
        ("--cwd=x",),
        ("--config=x",),
        ("--continue",),
        ("--resume=x",),
        ("--copy",),
        ("--takeover",),
        ("serve",),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--show-thinking=true",),
        ("--max-steps",),
        ("--permission-mode",),
    ],
)
def test_reasonix_reserved_and_unknown_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        reasonix.validate(arguments)


@pytest.mark.parametrize(
    "options",
    [
        Options(fast=False),
        Options(max_turns=3),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_reasonix_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "rx", options)


@pytest.mark.parametrize("selector", ["reasonix", "rx"])
def test_reasonix_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str], selector: str
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            selector,
            "--json",
            "--model",
            "provider-x",
            "--effort",
            "high",
            f"--prompt={prompt}",
            "--",
            "--permission-mode=ask",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    native = json.loads(payload["output"])
    assert status == payload["exit_code"] == payload["native_exit_code"] == 0
    assert native["argv"] == [
        "run",
        "--output-format",
        "json",
        "--model",
        "provider-x",
        "--effort",
        "high",
        "--permission-mode=ask",
    ]
    assert native["stdin"] == prompt and native["prompt"] == prompt.strip()
    assert payload["reported_models"] is payload["cost_usd"] is None
    assert payload["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "cached_input_tokens": 1,
        "cache_write_input_tokens": None,
        "reasoning_output_tokens": None,
    }


@pytest.mark.parametrize(
    "native_args", [("--permission-mode=plan",), ("--permission-mode", "plan")]
)
def test_reasonix_runner_preserves_headless_plan_failure(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    native_args: tuple[str, ...],
) -> None:
    status = main(
        ["--config", str(native_contract_config), "rx", "task", "--json", "--", *native_args]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert status == payload["exit_code"] == payload["native_exit_code"] == 2
    assert payload["output"] == ""
    assert payload["error"]["code"] == "native_exit"
    assert "--permission-mode plan requires an interactive session" in captured.err


@pytest.mark.parametrize(
    ("subtype", "is_error"),
    [
        ("incomplete_read", False),
        ("recovery_paused", False),
        ("completion_uncertain", False),
        ("error_during_execution", True),
    ],
)
def test_reasonix_incomplete_result_retains_text_and_usage(subtype: str, is_error: bool) -> None:
    decoded = reasonix.decode(json.dumps(result(subtype=subtype, is_error=is_error)))
    assert decoded.output == "answer" and decoded.usage == Usage(3, 1, None, 2)
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert subtype in decoded.error.message


def test_reasonix_accounting_does_not_mislabel_native_aliases() -> None:
    decoded = reasonix.decode(
        json.dumps(result(total_cost_usd=12, currency="CNY", model="not-reported"))
    )
    assert decoded.output == "answer" and decoded.error is None
    assert decoded.usage == Usage(3, 1, None, 2)
    assert decoded.reported_models is decoded.cost_usd is None


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "message"},
        {"subtype": "future"},
        {"subtype": []},
        {"is_error": 0},
        {"is_error": True},
        {"subtype": "error_during_execution", "is_error": False},
        {"usage": []},
        {"usage": {}},
        {"usage": {"input_tokens": True, "output_tokens": 1}},
        {"usage": {"input_tokens": -1, "output_tokens": 1}},
    ],
)
def test_reasonix_malformed_fields_keep_answer(fields: dict[str, object]) -> None:
    decoded = reasonix.decode(json.dumps(result(**fields)))
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "[]",
        "{}",
        "garbage",
        '{"type":"result","type":"result"}',
        "[" * 2000 + "]" * 2000,
        '{"wide":' + "1" * 5000 + "}",
        '{"wide":0.' + "1" * 200 + "}",
        json.dumps(result()) + json.dumps(result()),
    ],
)
def test_reasonix_hostile_whole_json_is_normalized(payload: str) -> None:
    decoded = reasonix.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_reasonix_nonfinite_numbers_preserve_valid_text(value: float) -> None:
    decoded = reasonix.decode(json.dumps(result(total_cost_usd=value)))
    assert decoded.output == "answer" and decoded.usage == Usage(3, 1, None, 2)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps(result(result="\ud800")),
        "\ud800",
        json.dumps(result(extra={"\ud800": "invalid key"})),
    ],
)
def test_reasonix_invalid_unicode_is_normalized(payload: str) -> None:
    decoded = reasonix.decode(payload)
    assert decoded.error is not None and decoded.error.code == "output_encoding"


def test_reasonix_empty_output_missing_usage_and_nested_valid_values() -> None:
    decoded = reasonix.decode(json.dumps(result(result="", usage=None, extra=[True, {}, 1.5])))
    assert decoded.output == "" and decoded.usage is None and decoded.error is None


def test_reasonix_oversize_output_is_normalized() -> None:
    decoded = reasonix.decode("x" * (8 * 1024 * 1024 + 1))
    assert decoded.error is not None and decoded.error.code == "stdout_limit_exceeded"


@pytest.mark.parametrize("native_exit", [0, 17])
def test_reasonix_runner_rejects_paused_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], native_exit: int
) -> None:
    payload = json.dumps(result(subtype="recovery_paused"))
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.reasonix]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "rx", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or 1)
    assert value["native_exit_code"] == native_exit and value["output"] == "answer"
    assert value["error"]["code"] == ("native_exit" if native_exit else "provider_error")


def test_reasonix_native_failure_outranks_malformed_optional_usage() -> None:
    decoded = reasonix.decode(json.dumps(result(subtype="recovery_paused", usage=[])))
    assert decoded.output == "answer" and decoded.usage is None
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "Reasonix run ended with recovery_paused."
