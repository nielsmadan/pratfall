import json
import subprocess
import sys
from pathlib import Path

import pytest

from pratfall import __version__
from pratfall.catalog import AGENTS
from pratfall.cli import main
from pratfall.config import init_config


@pytest.mark.parametrize("arguments", [["--help"], ["config", "--help"], ["agents", "--help"]])
def test_help_is_available(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(arguments)
    assert caught.value.code == 0
    assert "usage: prat" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out == f"prat {__version__}\n"


def test_no_arguments_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: prat" in capsys.readouterr().out


def test_top_level_help_exposes_run_contract(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "prat [RUN_OPTIONS] SELECTOR PROMPT [-- NATIVE_ARGS]" in output
    for flag in (
        "--prompt=TEXT",
        "--model MODEL",
        "--effort EFFORT",
        "--timeout SECONDS",
        "--cwd PATH",
        "--dry-run",
    ):
        assert flag in output
    assert 'prat simple "review this change" --effort low' in output
    assert "prat cc -" in output
    assert "prat cc --prompt=-leading-dash" in output


def test_agents_inventory_reports_aliases_and_capabilities(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["agents", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert [agent["name"] for agent in result["agents"]] == [agent.name for agent in AGENTS]
    kiro = next(agent for agent in result["agents"] if agent["name"] == "kiro")
    assert kiro["aliases"] == ["ki"]
    assert kiro["capabilities"]["model"] is True
    assert kiro["capabilities"]["effort_values"] == ["low", "medium", "high", "xhigh", "max"]
    hermes = next(agent for agent in result["agents"] if agent["name"] == "hermes")
    assert hermes["capabilities"]["effort_values"] == [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]


def test_agents_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents"]) == 0
    assert "claude (cc): model, effort, max_budget_usd, max_turns" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--config", "chosen.toml", "config", "path"],
        ["config", "--config", "chosen.toml", "path"],
        ["config", "path", "--config", "chosen.toml"],
    ],
)
def test_config_flags_work_at_each_management_level(
    arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(arguments) == 0
    assert capsys.readouterr().out == f"{tmp_path / 'chosen.toml'}\n"


@pytest.mark.parametrize("arguments", [["--json", "config", "path"], ["config", "path", "--json"]])
def test_json_flag_survives_subparser_defaults(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_init_and_validate_management(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.toml"
    assert main(["config", "init", "--config", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "path": str(path),
        "created": True,
    }
    assert main(["config", "validate", "--config", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "path": str(path),
        "exists": True,
        "valid": True,
    }
    assert main(["config", "validate", "--config", str(path)]) == 0
    assert capsys.readouterr().out == f"Valid config: {path}\n"


def test_default_validate_explains_missing_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "validate"]) == 0
    assert "using built-in defaults" in capsys.readouterr().out


def test_config_validate_checks_resolved_native_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'version=1\n[defaults]\nnative_args=["--output-format", "text"]\n'
        '[profiles.bad]\nagent="claude"\n',
        encoding="utf-8",
    )
    assert main(["config", "validate", "--config", str(path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert f"{path}: profiles.bad.native_args" in result["error"]["message"]
    assert "controlled by prat" in result["error"]["message"]


def test_profile_native_arguments_replace_invalid_defaults_during_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'version=1\n[defaults]\nnative_args=["--output-format", "text"]\n'
        '[profiles.safe]\nagent="claude"\nnative_args=["--verbose"]\n',
        encoding="utf-8",
    )
    assert main(["config", "validate", "--config", str(path)]) == 0
    assert capsys.readouterr().out == f"Valid config: {path}\n"


def test_profiles_list_resolved_model_and_effort(capsys: pytest.CaptureFixture[str]) -> None:
    init_config()
    assert main(["profiles"]) == 0
    assert capsys.readouterr().out == "simple: codex model=gpt-5.6-luna effort=low timeout=600.0\n"
    assert main(["profiles", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["profiles"] == [
        {
            "name": "simple",
            "agent": "codex",
            "options": {
                "model": "gpt-5.6-luna",
                "effort": "low",
                "timeout": 600.0,
                "max_budget_usd": None,
                "max_turns": None,
                "max_ai_credits": None,
                "native_args": [],
            },
        }
    ]


def test_profiles_text_lists_all_resolved_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """version=1
[profiles.inspect]
agent="claude"
model="model-id"
effort="high"
timeout=42
max_budget_usd=2.5
max_turns=3
native_args=["--permission-mode", "two words"]
""",
        encoding="utf-8",
    )
    assert main(["profiles", "--config", str(path)]) == 0
    assert capsys.readouterr().out == (
        "inspect: claude model=model-id effort=high timeout=42.0 "
        'max_budget_usd=2.5 max_turns=3 native_args=["--permission-mode", "two words"]\n'
    )


def test_empty_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profiles"]) == 0
    assert capsys.readouterr().out == "No profiles configured.\n"


def test_profile_without_optional_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('version=1\n[profiles.basic]\nagent="claude"\n', encoding="utf-8")
    assert main(["profiles", "--config", str(path)]) == 0
    assert capsys.readouterr().out == "basic: claude timeout=600.0\n"


def test_doctor_locates_wrappers_without_executing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    wrapper = tmp_path / "wrapper"
    wrapper.write_text("invalid executable content", encoding="utf-8")
    wrapper.chmod(0o700)
    path = tmp_path / "config.toml"
    path.write_text('version=1\n[agents.codex]\ncommand=["./wrapper", "codex"]\n', encoding="utf-8")
    assert main(["doctor", "--config", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    codex = next(agent for agent in result["agents"] if agent["agent"] == "codex")
    assert codex == {
        "agent": "codex",
        "executable": str(wrapper),
        "path": str(wrapper),
        "available": True,
    }
    claude = next(agent for agent in result["agents"] if agent["agent"] == "claude")
    assert claude == {"agent": "claude", "executable": "claude", "path": None, "available": False}
    assert main(["doctor", "--config", str(path)]) == 0
    assert f"codex: {wrapper}\n" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments", [["unknown"], ["config"], ["agents", "--bad"], ["--con", "x"]]
)
def test_syntax_errors_return_two(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(arguments) == 2
    assert capsys.readouterr().err.startswith("prat: ")


def test_syntax_errors_honor_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "unknown"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert result["error"]["code"] == "invalid_arguments"


def test_config_errors_honor_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "missing.toml"
    assert main(["profiles", "--config", str(path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert result["error"]["message"] == f"{path}: config file does not exist."


def test_unknown_user_config_path_error_honors_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profiles", "--config", "~definitely_no_such_user_xyz/config.toml", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert result["error"]["code"] == "invalid_config"
    assert "absolute path or a valid ~user path" in result["error"]["message"]


def test_json_after_delimiter_is_not_an_output_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--", "--json"]) == 2
    assert capsys.readouterr().err.startswith("prat: ")


def test_module_entry_point_works_outside_repo(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pratfall", "--version"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == f"prat {__version__}\n"
