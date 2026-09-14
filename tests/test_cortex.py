import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import resolved as resolved_profile
from pratfall.adapters import cortex
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile


def test_cortex_builder_keeps_global_controls_before_exec_and_literal_stdin() -> None:
    prompt = "-雪\n$HOME `id` $(touch forbidden)\n".encode()
    invocation = cortex.build(
        ResolvedProfile(
            BY_NAME["cortex"],
            None,
            ("wrapper", "$HOME"),
            Options(model="model-x", effort="max", max_turns=3, native_args=("-c", "local")),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--model",
        "model-x",
        "--effort",
        "max",
        "--max-turns",
        "3",
        "-c",
        "local",
        "exec",
        "--file",
        "-",
    )
    assert invocation.stdin == prompt


def test_cortex_defaults_add_only_exec_stdin_controls() -> None:
    invocation = cortex.build(
        ResolvedProfile(BY_NAME["cortex"], None, ("cortex",), Options()), b"task"
    )
    assert invocation.argv == ("cortex", "exec", "--file", "-")
    assert invocation.stdin == b"task"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--file=x",),
        ("-fx",),
        ("--print=x",),
        ("-px",),
        ("--prompt=x",),
        ("--output-format=stream-json",),
        ("--model=x",),
        ("-mx",),
        ("--effort=high",),
        ("--max-turns=2",),
        ("--workdir=x",),
        ("-wx",),
        ("--cwd=x",),
        ("--config=x",),
        ("--resume=x",),
        ("-rx",),
        ("--session=x",),
        ("--continue",),
        ("--private",),
        ("--plan",),
        ("--auto-accept-plans",),
        ("--bypass",),
        ("--dangerously-allow-all-tool-calls",),
        ("--cloud",),
        ("--cloud=x",),
        ("--no-workspace",),
        ("--github=x",),
        ("--github", "secret"),
        ("--executor=x",),
        ("--remote=x",),
        ("--help",),
        ("exec",),
        ("update",),
        ("--unknown",),
        ("@args",),
        ("--connection",),
        ("-c",),
        ("-clocal",),
    ],
)
def test_cortex_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError) as failure:
        cortex.validate(resolved_profile("cortex", Options(native_args=arguments)))
    assert failure.value.code == "invalid_arguments"


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "max"])
def test_cortex_verified_effort_enum(native_contract_config: Path, effort: str) -> None:
    resolved = resolve_profile(load_config(native_contract_config), "co", Options(effort=effort))
    assert cortex.build(resolved, b"task").argv[-5:] == ("--effort", effort, "exec", "--file", "-")


def test_cortex_rejects_unverified_effort(native_contract_config: Path) -> None:
    with pytest.raises(PratError, match="effort"):
        resolve_profile(load_config(native_contract_config), "co", Options(effort="xhigh"))


@pytest.mark.parametrize(
    "options", [Options(fast=False), Options(max_budget_usd=1), Options(max_ai_credits=1)]
)
def test_cortex_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "co", options)


@pytest.mark.parametrize("selector", ["cortex", "co", "cortex-review"])
def test_cortex_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    with native_contract_config.open("a") as config:
        config.write('\n[profiles.cortex-review]\nagent="co"\nmodel="profile-model"\n')
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
            "--max-turns",
            "3",
            f"--prompt={prompt}",
            "--",
            "--connection=local",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "cortex" and result["status"] == "success"
    assert result["profile"] == ("cortex-review" if selector == "cortex-review" else None)
    assert native["argv"] == [
        "--model",
        "model-x",
        "--effort",
        "high",
        "--max-turns",
        "3",
        "--connection=local",
        "exec",
        "--file",
        "-",
    ]
    assert native["stdin"] == native["prompt"] == prompt
    assert result["usage"] is result["reported_models"] is result["cost_usd"] is None


@pytest.mark.parametrize("output", ["", "Error: generated prose", "banner\nanswer café 雪 \t"])
def test_cortex_text_capture_removes_only_terminal_line_endings(output: str) -> None:
    decoded = cortex.decode(output + "\r\n\n")
    assert decoded.output == output and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


@pytest.mark.parametrize("native_exit", [0, 17])
def test_cortex_native_exit_controls_success(
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
    config.write_text(f"version=1\n[agents.cortex]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "co", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == result["exit_code"] == result["native_exit_code"] == native_exit
    assert result["status"] == ("success" if native_exit == 0 else "error")
    assert result["output"] == "Error: partial answer"
    assert (result["error"]["code"] if native_exit else result["error"]) == (
        "native_exit" if native_exit else None
    )
