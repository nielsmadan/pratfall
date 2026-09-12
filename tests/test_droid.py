import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import droid
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def result(**fields: object) -> dict[str, object]:
    return {"type": "result", "subtype": "success", "is_error": False, "result": "answer", **fields}


def test_droid_literal_stdin_and_native_controls() -> None:
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = droid.build(
        ResolvedProfile(
            BY_NAME["droid"],
            None,
            ("wrapper", "$HOME"),
            Options(model="model-x", effort="dynamic", native_args=("--auto=low",)),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "exec",
        "--output-format",
        "json",
        "--model",
        "model-x",
        "--reasoning-effort",
        "dynamic",
        "--auto=low",
    )
    assert invocation.stdin == prompt


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=x",),
        ("-fpath",),
        ("--input-format=stream-json",),
        ("-otext",),
        ("--model=x",),
        ("-rhigh",),
        ("--spec-model=x",),
        ("--session-id=x",),
        ("--fork=x",),
        ("--cwd=x",),
        ("--worktree",),
        ("--config=x",),
        ("--mission",),
        ("--list-tools",),
        ("--remote=x",),
        ("--unknown",),
        ("login",),
        ("@args",),
        ("--auto",),
        ("--skip-permissions-unsafe",),
    ],
)
def test_droid_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        droid.validate(arguments)


@pytest.mark.parametrize(
    "options",
    [
        Options(fast=False),
        Options(max_turns=3),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_droid_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "dr", options)


def test_droid_effort_union_is_validated(native_contract_config: Path) -> None:
    expected = ("none", "dynamic", "off", "minimal", "low", "medium", "high", "xhigh", "max")
    assert BY_NAME["droid"].capabilities.effort_values == expected
    config = load_config(native_contract_config)
    for effort in expected:
        assert resolve_profile(config, "dr", Options(effort=effort)).options.effort == effort
    with pytest.raises(PratError, match="Droid accepts:"):
        resolve_profile(config, "dr", Options(effort="ultra"))


@pytest.mark.parametrize("selector", ["droid", "dr"])
def test_droid_independent_native_contract(
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
            "--effort",
            "high",
            f"--prompt={prompt}",
            "--",
            "--auto=low",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    native = json.loads(payload["output"])
    assert native == json.loads((native_contract_config.parent / "native-calls.jsonl").read_text())
    assert native["argv"] == [
        "exec",
        "--output-format",
        "json",
        "--model",
        "model-x",
        "--reasoning-effort",
        "high",
        "--auto=low",
    ]
    assert native["stdin"] == native["prompt"] == prompt
    assert payload["usage"] is payload["reported_models"] is payload["cost_usd"] is None


@pytest.mark.parametrize("answer", ["", "answer", "The task failed: generated prose"])
def test_droid_success_requires_envelope_not_prose(answer: str) -> None:
    decoded = droid.decode(json.dumps(result(result=answer, usage={"input_tokens": 4}, model="x")))
    assert decoded.output == answer and decoded.error is None
    assert decoded.usage is decoded.reported_models is decoded.cost_usd is None


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "message"},
        {"subtype": "future"},
        {"is_error": 0},
        {"subtype": []},
    ],
)
def test_droid_malformed_completion_retains_answer(fields: dict[str, object]) -> None:
    decoded = droid.decode(json.dumps(result(**fields)))
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
        json.dumps(result(result=None)),
    ],
)
def test_droid_hostile_json_is_normalized(payload: str) -> None:
    decoded = droid.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_droid_nonfinite_metadata_preserves_answer(number: str) -> None:
    payload = json.dumps(result())[:-1] + ', "metadata":' + number + "}"
    decoded = droid.decode(payload)
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    ("payload", "output"),
    [
        (json.dumps(result(result="\ud800")), ""),
        ("\ud800", ""),
        (json.dumps(result(extra={"\ud800": "invalid key"})), "answer"),
    ],
)
def test_droid_invalid_unicode_is_normalized(payload: str, output: str) -> None:
    decoded = droid.decode(payload)
    assert decoded.output == output
    assert decoded.error is not None and decoded.error.code == "output_encoding"


def test_droid_oversize_output_is_normalized() -> None:
    decoded = droid.decode("x" * (8 * 1024 * 1024 + 1))
    assert decoded.error is not None and decoded.error.code == "stdout_limit_exceeded"


def test_droid_provider_failure_outranks_optional_metadata() -> None:
    decoded = droid.decode(json.dumps(result(is_error=True, subtype="error", extra=float("nan"))))
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "provider_error"


@pytest.mark.parametrize("native_exit", [0, 17])
def test_droid_runner_preserves_structured_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    payload = json.dumps(result(is_error=True, subtype="error"))
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.droid]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "dr", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or 1)
    assert value["native_exit_code"] == native_exit and value["output"] == "answer"
    assert value["error"]["code"] == ("native_exit" if native_exit else "provider_error")
