import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import vibe
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def message(text: str = "answer", **fields: object) -> dict[str, object]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        **fields,
    }


def test_vibe_bare_prompt_stdin_and_native_budgets() -> None:
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = vibe.build(
        ResolvedProfile(
            BY_NAME["vibe"],
            None,
            ("wrapper", "$HOME"),
            Options(max_turns=3, max_budget_usd=0.5, native_args=("--max-tokens=123",)),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--prompt",
        "--output",
        "json",
        "--max-turns",
        "3",
        "--max-price",
        "0.5",
        "--max-tokens=123",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=x",),
        ("-px",),
        ("--output=text",),
        ("--max-turns=2",),
        ("--max-price=2",),
        ("--model=x",),
        ("--effort=high",),
        ("--agent=x",),
        ("--workdir=x",),
        ("--worktree",),
        ("--config=x",),
        ("--resume=x",),
        ("--continue",),
        ("--teleport",),
        ("--teleport=true",),
        ("--remote=x",),
        ("--setup",),
        ("--check-upgrade",),
        ("--legacy-harness",),
        ("update",),
        ("--unknown",),
        ("@args",),
        ("--max-tokens",),
        ("--enabled-tools",),
    ],
)
def test_vibe_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        vibe.validate(arguments)


@pytest.mark.parametrize(
    "options",
    [
        Options(model="model-x"),
        Options(effort="high"),
        Options(fast=False),
        Options(max_ai_credits=1),
    ],
)
def test_vibe_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "vibe", options)


def test_vibe_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            "vibe",
            "--json",
            "--max-turns",
            "3",
            "--max-budget-usd",
            "0.5",
            f"--prompt={prompt}",
            "--",
            "--max-tokens=123",
            "--enabled-tools",
            "read_*",
            "--disabled-tools=write_*",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    native = json.loads(payload["output"])
    assert native == json.loads((native_contract_config.parent / "native-calls.jsonl").read_text())
    assert native["argv"] == [
        "--prompt",
        "--output",
        "json",
        "--max-turns",
        "3",
        "--max-price",
        "0.5",
        "--max-tokens=123",
        "--enabled-tools",
        "read_*",
        "--disabled-tools=write_*",
    ]
    assert native["stdin"] == prompt and native["prompt"] == prompt.strip()
    assert payload["usage"] is payload["reported_models"] is payload["cost_usd"] is None


def test_vibe_uses_last_nonempty_assistant_text_and_native_block_joining() -> None:
    history = [
        message("system", role="system"),
        message("user", role="user"),
        message("earlier"),
        {"type": "reasoning", "text": "hidden"},
        {"type": "effect", "state": {"status": "failed", "error": {"message": "recoverable"}}},
        {"type": "callback"},
        {"type": "checkpoint"},
        {"type": "notice", "level": "error", "message": "nonfatal notice"},
        message(
            content=[
                {"type": "text", "text": "last"},
                {"type": "image", "attachment": {}},
                {"type": "text", "text": ""},
                {"type": "text", "text": "雪"},
                {"type": "resource", "resource": {}},
            ]
        ),
        message(""),
        message(content=[]),
    ]
    decoded = vibe.decode(json.dumps(history))
    assert decoded.output == "last\n\n\n\n雪" and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


@pytest.mark.parametrize(
    "history", [[], [message("")], [message(content=[])], [message(role="user")]]
)
def test_vibe_complete_history_can_have_empty_output(history: list[object]) -> None:
    decoded = vibe.decode(json.dumps(history))
    assert decoded.output == "" and decoded.error is None


@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        {},
        {"type": []},
        {"type": "future"},
        message(role=[]),
        message(content="answer"),
        message(content=[None]),
        message(content=[{"type": "text", "text": None}]),
        message(content=[{"type": "future"}]),
    ],
)
def test_vibe_malformed_history_retains_earlier_answer(entry: object) -> None:
    decoded = vibe.decode(json.dumps([message(), entry]))
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{}",
        "null",
        "garbage",
        json.dumps({"history": [message()], "teleportUrl": "remote"}),
        '[{"type":"message","type":"message"}]',
        "[" * 2000 + "]" * 2000,
        '[{"wide":' + "1" * 5000 + "}]",
        '[{"wide":0.' + "1" * 200 + "}]",
        json.dumps([message()]) + json.dumps([message()]),
    ],
)
def test_vibe_hostile_or_remote_json_is_normalized(payload: str) -> None:
    decoded = vibe.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_vibe_nonfinite_metadata_preserves_answer(number: str) -> None:
    payload = json.dumps([message()])[:-2] + ', "metadata":' + number + "}]"
    decoded = vibe.decode(payload)
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    ("payload", "output"),
    [
        (json.dumps([message(), message("\ud800")]), "answer"),
        ("\ud800", ""),
        (json.dumps([message(extra={"\ud800": "invalid key"})]), "answer"),
    ],
)
def test_vibe_invalid_unicode_is_normalized(payload: str, output: str) -> None:
    decoded = vibe.decode(payload)
    assert decoded.output == output
    assert decoded.error is not None and decoded.error.code == "output_encoding"


def test_vibe_oversize_output_is_normalized() -> None:
    decoded = vibe.decode("x" * (8 * 1024 * 1024 + 1))
    assert decoded.error is not None and decoded.error.code == "stdout_limit_exceeded"


@pytest.mark.parametrize(
    ("stdout", "native_exit", "output", "error"),
    [
        ("[]", 0, "", None),
        (json.dumps([message()]), 17, "answer", "native_exit"),
        ("", 1, "", "native_exit"),
        ("", 0, "", "protocol_error"),
    ],
)
def test_vibe_runner_eof_and_native_error_precedence(
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
    config.write_text(f"version=1\n[agents.vibe]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "vibe", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or int(error is not None))
    assert value["native_exit_code"] == native_exit and value["output"] == output
    assert (value["error"]["code"] if error else value["error"]) == error
