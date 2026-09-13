import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import crush
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def test_crush_builder_preserves_literal_prefix_and_stdin() -> None:
    prompt = "-雪\n$HOME `id` $(touch forbidden)\n".encode()
    invocation = crush.build(
        ResolvedProfile(
            BY_NAME["crush"],
            None,
            ("wrapper", "$HOME"),
            Options(model="provider/model", native_args=("--verbose", "-d")),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "run",
        "--quiet",
        "--model",
        "provider/model",
        "--verbose",
        "-d",
    )
    assert invocation.stdin == prompt


def test_crush_defaults_add_only_one_shot_controls() -> None:
    invocation = crush.build(
        ResolvedProfile(BY_NAME["crush"], None, ("crush",), Options()), b"task"
    )
    assert invocation.argv == ("crush", "run", "--quiet")
    assert invocation.stdin == b"task"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--quiet=false",),
        ("--model=other",),
        ("-mother",),
        ("--small-model=x",),
        ("--session=x",),
        ("-sx",),
        ("--continue",),
        ("-C",),
        ("--cwd=x",),
        ("-cx",),
        ("--data-dir=x",),
        ("-Dx",),
        ("--host=x",),
        ("-Hx",),
        ("--channels=x",),
        ("--config=x",),
        ("--prompt=x",),
        ("--file=x",),
        ("--output-format=json",),
        ("--executor=x",),
        ("--remote=x",),
        ("--cloud=x",),
        ("--yolo",),
        ("--help",),
        ("run",),
        ("login",),
        ("--unknown",),
        ("@args",),
        ("--verbose=true",),
    ],
)
def test_crush_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError) as failure:
        crush.validate(arguments)
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
def test_crush_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "cr", options)


@pytest.mark.parametrize("selector", ["crush", "cr", "crush-review"])
def test_crush_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    with native_contract_config.open("a") as config:
        config.write('\n[profiles.crush-review]\nagent="cr"\nmodel="profile-model"\n')
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
            "--debug",
            "-v",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "crush" and result["status"] == "success"
    assert result["profile"] == ("crush-review" if selector == "crush-review" else None)
    assert native["argv"] == ["run", "--quiet", "--model", "model-x", "--debug", "-v"]
    assert native["stdin"] == prompt
    assert native["prompt"] == prompt + "\n\n"
    assert result["usage"] is result["reported_models"] is result["cost_usd"] is None


@pytest.mark.parametrize("output", ["", "Error: generated prose", "banner\nanswer café 雪 \t"])
def test_crush_text_capture_removes_only_terminal_line_endings(output: str) -> None:
    decoded = crush.decode(output + "\r\n\n")
    assert decoded.output == output and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


@pytest.mark.parametrize("native_exit", [0, 17])
def test_crush_native_exit_controls_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; print('Error: partial answer'); sys.exit({native_exit})",
    ]
    config = tmp_path / "result.toml"
    config.write_text(f"version=1\n[agents.crush]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "cr", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == result["exit_code"] == result["native_exit_code"] == native_exit
    assert result["status"] == ("success" if native_exit == 0 else "error")
    assert result["output"] == "Error: partial answer"
    assert (result["error"]["code"] if native_exit else result["error"]) == (
        "native_exit" if native_exit else None
    )
