import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import warp
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, ResultError


def stream(*events: object) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def test_warp_builder_keeps_model_native_options_and_literal_prompt() -> None:
    options = Options(
        model="configured-model",
        native_args=("--name", "task", "--strict-mcp-startup", "--mcp-startup-timeout=10s"),
    )
    resolved = ResolvedProfile(BY_NAME["warp"], None, ("wrapper", "literal $HOME"), options)
    invocation = warp.build(resolved, "-雪\n$(touch /tmp/never)\n".encode())
    assert invocation.argv == (
        "wrapper",
        "literal $HOME",
        "agent",
        "run",
        "--output-format",
        "ndjson",
        "--model",
        "configured-model",
        "--name",
        "task",
        "--strict-mcp-startup",
        "--mcp-startup-timeout=10s",
        "--prompt=-雪\n$(touch /tmp/never)\n",
    )
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=other",),
        ("-pother",),
        ("--file", "-"),
        ("--saved-prompt=x",),
        ("--output-format=json",),
        ("--model=x",),
        ("--cwd=x",),
        ("-Cx",),
        ("--config-file=x",),
        ("--profile=x",),
        ("--conversation=x",),
        ("--environment=x",),
        ("--runner=x",),
        ("--executor=x",),
        ("--harness=x",),
        ("--idle-on-complete=10s",),
        ("--share",),
        ("run-cloud",),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--name",),
        ("--strict-mcp-startup=true",),
    ],
)
def test_warp_rejects_reserved_and_unknown_native_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError) as failure:
        warp.validate(arguments)
    assert failure.value.code == "invalid_arguments"


@pytest.mark.parametrize("arguments", [("-nlabel",), ("--name=label",), ("-n", "label")])
def test_warp_accepts_verified_name_grammar(arguments: tuple[str, ...]) -> None:
    warp.validate(arguments)


@pytest.mark.parametrize(
    "options",
    [
        Options(effort="high"),
        Options(fast=False),
        Options(max_turns=2),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_warp_rejects_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "wp", options)


@pytest.mark.parametrize("selector", ["wp", "warp"])
def test_warp_cli_satisfies_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            selector,
            "--json",
            "--model",
            "model-x",
            f"--prompt={prompt}",
            "--",
            "--name=label",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "warp" and result["status"] == "success"
    assert native["argv"] == [
        "agent",
        "run",
        "--output-format",
        "ndjson",
        "--model",
        "model-x",
        "--name=label",
        f"--prompt={prompt}",
    ]
    assert native["stdin"] == "" and native["prompt"] == prompt
    assert (result["usage"], result["reported_models"], result["cost_usd"]) == (None, None, None)


def test_warp_recovers_tool_errors_joins_agent_text_and_preserves_duplicates() -> None:
    payload = stream(
        {"type": "system", "event_type": "conversation_started", "conversation_id": "x"},
        {"type": "agent_reasoning", "text": "reasoning"},
        {"type": "tool_error", "error": "permission denied"},
        {"type": "agent", "text": "café 雪"},
        {"type": "agent", "text": "café 雪"},
    )
    incremental = warp.consumer()
    for byte in payload.rstrip("\n").encode():
        incremental.feed(bytes((byte,)))
    decoded = incremental.finish()
    assert decoded.output == "café 雪\ncafé 雪" and decoded.error is None


@pytest.mark.parametrize("payload", ["", " \r\n", stream({"type": "tool_canceled"})])
def test_warp_native_eof_protocol_allows_no_text(payload: str) -> None:
    decoded = warp.decode(payload)
    assert decoded.output == "" and decoded.error is None
    assert decoded.usage is decoded.reported_models is decoded.cost_usd is None


@pytest.mark.parametrize(
    "suffix",
    [
        "not json\n",
        "{}\n",
        "[]\n",
        stream({"type": "agent"}),
        stream({"type": "agent", "text": []}),
        stream({"type": "future"}),
        "[" * 2000 + "]" * 2000,
        '{"type":"system","n":' + "1" * 129 + "}\n",
    ],
)
def test_warp_malformed_stream_retains_partial_text(suffix: str) -> None:
    decoded = warp.decode(stream({"type": "agent", "text": "prior"}) + suffix)
    assert decoded.output == "prior"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_warp_rejects_invalid_unicode() -> None:
    decoded = warp.decode('{"type":"agent","text":"\\ud800"}\n')
    assert decoded.error == ResultError(
        "output_encoding", "Agent output contains invalid Unicode text."
    )


@pytest.mark.parametrize("limits", [ConsumerLimits(state_bytes=5), ConsumerLimits(records=1)])
def test_warp_bounds_text_and_retained_records(limits: ConsumerLimits) -> None:
    incremental = warp.consumer(limits)
    incremental.feed(stream({"type": "agent", "text": "prior"}).encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(stream({"type": "agent", "text": "next"}).encode())
    assert failure.value.error.code == "stdout_limit_exceeded"
    with pytest.raises(ConsumerFailure) as final:
        incremental.finish()
    assert final.value.decoded is not None and final.value.decoded.output == "prior"


def test_warp_real_runner_preserves_native_failure_and_partial_answer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = stream({"type": "agent", "text": "partial"}) + "native failure text\n"
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit(17)"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.warp]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "wp", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == result["exit_code"] == result["native_exit_code"] == 17
    assert result["output"] == "partial" and result["status"] == "error"
    assert result["error"]["code"] == "native_exit"
