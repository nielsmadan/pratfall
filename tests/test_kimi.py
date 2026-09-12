import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import kimi
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def message(content: object = "answer", **fields: object) -> str:
    return json.dumps({"role": "assistant", "content": content, **fields})


def test_kimi_literal_stdin_and_boolean_thinking() -> None:
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = kimi.build(
        ResolvedProfile(
            BY_NAME["kimi"],
            None,
            ("wrapper", "$HOME"),
            Options(model="model-x", native_args=("--no-thinking", "--plan")),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--final-message-only",
        "--model",
        "model-x",
        "--no-thinking",
        "--plan",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=x",),
        ("-cx",),
        ("--print",),
        ("--quiet",),
        ("--input-format=x",),
        ("--output-format=x",),
        ("--final-message-only",),
        ("-mmodel",),
        ("--config=x",),
        ("--config-file=x",),
        ("-wpath",),
        ("--session=x",),
        ("--resume=x",),
        ("--continue",),
        ("--acp",),
        ("--wire",),
        ("--remote=x",),
        ("login",),
        ("--thinking=true",),
        ("--unknown",),
        ("@args",),
    ],
)
def test_kimi_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        kimi.validate(arguments)


@pytest.mark.parametrize(
    "options",
    [
        Options(effort="high"),
        Options(fast=False),
        Options(max_turns=3),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_kimi_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "km", options)


@pytest.mark.parametrize("selector", ["kimi", "km"])
def test_kimi_independent_native_contract(
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
            "--thinking",
            "--debug",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    native = json.loads(payload["output"])
    assert native == json.loads((native_contract_config.parent / "native-calls.jsonl").read_text())
    assert native["argv"] == [
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--final-message-only",
        "--model",
        "model-x",
        "--thinking",
        "--debug",
    ]
    assert native["stdin"] == prompt and native["prompt"] == prompt.strip()
    assert payload["usage"] is payload["reported_models"] is payload["cost_usd"] is None


@pytest.mark.parametrize("payload", ["", " \r\n", message("")])
def test_kimi_empty_final_output_is_success(payload: str) -> None:
    decoded = kimi.decode(payload)
    assert decoded.output == "" and decoded.error is None


def test_kimi_last_assistant_message_and_split_unicode() -> None:
    consumer = kimi.consumer()
    payload = (
        message("earlier") + "\r\n" + message("last 雪") + "\n" + message("last 雪")
    ).encode()
    for byte in payload:
        consumer.feed(bytes([byte]))
    decoded = consumer.finish()
    assert decoded.output == "last 雪" and decoded.error is None
    assert decoded.usage is decoded.reported_models is decoded.cost_usd is None


@pytest.mark.parametrize(
    "payload",
    [
        "garbage",
        "[]",
        "{}",
        message([]),
        message(None),
        message(role="user"),
        '{"type":"result","result":"answer"}',
        message(type="tool", content={"text": "bad"}),
        "[" * 2000 + "]" * 2000,
        '{"role":"assistant","content":' + "1" * 5000 + "}",
    ],
)
def test_kimi_malformed_records_preserve_emitted_answer(payload: str) -> None:
    decoded = kimi.decode(message("partial") + "\n" + payload)
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_kimi_invalid_unicode_preserves_previous_answer() -> None:
    decoded = kimi.decode(message("partial") + "\n" + message("\ud800"))
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "output_encoding"


def test_kimi_state_replacement_and_overflow_retains_last_answer() -> None:
    consumer = kimi.consumer(ConsumerLimits(state_bytes=5))
    consumer.feed((message("first") + "\n" + message("last") + "\n").encode())
    with pytest.raises(ConsumerFailure):
        consumer.feed((message("too long") + "\n").encode())
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"
    assert failure.value.decoded is not None and failure.value.decoded.output == "last"


@pytest.mark.parametrize(
    ("stdout", "native_exit", "output", "error"),
    [
        ("", 0, "", None),
        (message("partial") + "\nAuthentication failed", 17, "partial", "native_exit"),
        ("Authentication failed", 2, "", "native_exit"),
        (message("partial") + "\nAuthentication failed", 0, "partial", "protocol_error"),
    ],
)
def test_kimi_runner_eof_and_native_error_precedence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stdout: str,
    native_exit: int,
    output: str,
    error: str | None,
) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write({stdout!r}); sys.exit({native_exit})",
    ]
    config = tmp_path / "result.toml"
    config.write_text(f"version=1\n[agents.kimi]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "km", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or int(error is not None))
    assert value["native_exit_code"] == native_exit and value["output"] == output
    assert (value["error"]["code"] if error else value["error"]) == error
