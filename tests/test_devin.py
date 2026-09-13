import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import devin
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def test_devin_builder_protects_literal_prompt_after_delimiter() -> None:
    prompt = "-雪\n$HOME `id` $(touch forbidden)\n"
    invocation = devin.build(
        ResolvedProfile(
            BY_NAME["devin"],
            None,
            ("wrapper", "$HOME"),
            Options(model="opus", native_args=("--permission-mode=normal",)),
        ),
        prompt.encode(),
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "-p",
        "--model",
        "opus",
        "--permission-mode=normal",
        "--",
        prompt,
    )
    assert invocation.stdin == b""


def test_devin_defaults_preserve_native_trust_and_permissions() -> None:
    invocation = devin.build(
        ResolvedProfile(BY_NAME["devin"], None, ("devin",), Options()), b"task"
    )
    assert invocation.argv == ("devin", "-p", "--", "task")
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "arguments",
    [
        ("--print=x",),
        ("-px",),
        ("--prompt=x",),
        ("--prompt-file=-",),
        ("--file=x",),
        ("--output-format=json",),
        ("--export",),
        ("--model=x",),
        ("--continue",),
        ("-c",),
        ("--resume=x",),
        ("-rx",),
        ("--config=x",),
        ("--cwd=x",),
        ("--respect-workspace-trust=false",),
        ("--respect-workspace-trust", "false"),
        ("--executor=x",),
        ("--remote=x",),
        ("--cloud=x",),
        ("--help",),
        ("auth",),
        ("setup",),
        ("--", "prompt"),
        ("--unknown",),
        ("@args",),
        ("--permission-mode",),
        ("--permission-mode", "--continue"),
    ],
)
def test_devin_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError) as failure:
        devin.validate(arguments)
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
def test_devin_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "dv", options)


@pytest.mark.parametrize("selector", ["devin", "dv", "devin-review"])
def test_devin_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    with native_contract_config.open("a") as config:
        config.write('\n[profiles.devin-review]\nagent="dv"\nmodel="profile-model"\n')
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
            "--permission-mode",
            "normal",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "devin" and result["status"] == "success"
    assert result["profile"] == ("devin-review" if selector == "devin-review" else None)
    assert native["argv"] == [
        "-p",
        "--model",
        "model-x",
        "--permission-mode",
        "normal",
        "--",
        prompt,
    ]
    assert native["stdin"] == "" and native["prompt"] == prompt
    assert result["usage"] is result["reported_models"] is result["cost_usd"] is None


@pytest.mark.parametrize("output", ["", "Error: generated prose", "banner\nanswer café 雪 \t"])
def test_devin_text_capture_removes_only_terminal_line_endings(output: str) -> None:
    decoded = devin.decode(output + "\r\n\n")
    assert decoded.output == output and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


@pytest.mark.parametrize(
    ("native_exit", "stdout", "stderr"),
    [
        (0, "Error: generated answer\n", ""),
        (17, "partial answer\n", "native failure"),
        (1, "", "Workspace is not trusted"),
    ],
)
def test_devin_native_failure_including_untrusted_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
    stdout: str,
    stderr: str,
) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r}); sys.exit({native_exit})",
    ]
    config = tmp_path / "result.toml"
    config.write_text(f"version=1\n[agents.devin]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "dv", "task", "--json"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert status == result["exit_code"] == result["native_exit_code"] == native_exit
    assert result["status"] == ("success" if native_exit == 0 else "error")
    assert result["output"] == stdout.rstrip("\r\n")
    if stderr:
        assert stderr + "\n" in captured.err
    else:
        assert "prat: Devin finished with status success" in captured.err
    assert (result["error"]["code"] if native_exit else result["error"]) == (
        "native_exit" if native_exit else None
    )
