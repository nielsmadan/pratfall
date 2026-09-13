import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import iflow
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def test_iflow_builder_keeps_literal_prompt_and_native_modes() -> None:
    options = Options(model="byok-model", native_args=("--thinking", "--default"))
    resolved = ResolvedProfile(BY_NAME["iflow"], None, ("wrapper", "literal $HOME"), options)
    invocation = iflow.build(resolved, "-雪\n$(touch /tmp/never)\n".encode())
    assert invocation.argv == (
        "wrapper",
        "literal $HOME",
        "--model",
        "byok-model",
        "--thinking",
        "--default",
        "--prompt=-雪\n$(touch /tmp/never)\n",
    )
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "arguments",
    [
        ("--prompt=other",),
        ("-pother",),
        ("--prompt-interactive=x",),
        ("--continue",),
        ("--resume=x",),
        ("-rx",),
        ("--model=x",),
        ("--output-file=x",),
        ("--stream",),
        ("--experimental-acp",),
        ("--port=123",),
        ("--max-turns=3",),
        ("--timeout=30",),
        ("--cwd=x",),
        ("--config=x",),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--thinking=true",),
    ],
)
def test_iflow_rejects_reserved_and_unknown_native_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError) as failure:
        iflow.validate(arguments)
    assert failure.value.code == "invalid_arguments"


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
def test_iflow_rejects_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "if", options)


@pytest.mark.parametrize("selector", ["if", "iflow"])
def test_iflow_cli_satisfies_independent_native_contract(
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
            "--plan",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "iflow" and result["status"] == "success"
    assert native["argv"] == ["--model", "model-x", "--plan", f"--prompt={prompt}"]
    assert native["stdin"] == "" and native["prompt"] == prompt
    assert (result["usage"], result["reported_models"], result["cost_usd"]) == (None, None, None)


@pytest.mark.parametrize("output", ["", "native error: generated prose", "banner\nanswer café 雪 "])
def test_iflow_captures_stdout_without_inferred_semantic_failure(output: str) -> None:
    decoded = iflow.decode(output + "\r\n\n")
    assert decoded.output == output and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


@pytest.mark.parametrize("native_exit", [0, 17])
def test_iflow_native_exit_is_authoritative(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; print('Error: generated answer'); sys.exit({native_exit})",
    ]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.iflow]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "if", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == result["exit_code"] == result["native_exit_code"] == native_exit
    assert result["output"] == "Error: generated answer"
    assert result["status"] == ("success" if native_exit == 0 else "error")
