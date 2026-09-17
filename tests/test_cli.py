import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from types import FrameType

import pytest

from pratfall import __version__
from pratfall.catalog import AGENTS
from pratfall.cli import doctor as doctor_module
from pratfall.cli import main
from pratfall.cli.doctor import _doctor, _doctor_inventory, _doctor_line, _version_result
from pratfall.config import init_config, load_config
from pratfall.interruption import InterruptionState
from pratfall.models import Config, Invocation, RawCapture
from pratfall.runner import OutputLimits, ProcessResult
from pratfall.runner import run as run_process


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


def test_trace_without_selector_uses_normalized_run_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--trace", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["agent"] is None
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "A selector is required.",
    }


def test_top_level_help_exposes_run_contract(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "prat [RUN_OPTIONS] SELECTOR PROMPT [-- NATIVE_ARGS]" in output
    for flag in (
        "--prompt=TEXT",
        "-f, --file PATH",
        "--model MODEL",
        "--effort EFFORT",
        "--fast / --no-fast",
        "--timeout SECONDS",
        "--cwd PATH",
        "--trace",
        "--dry-run",
    ):
        assert flag in output
    assert 'prat simple "review this change" --effort low' in output
    assert "prat cc -" in output
    assert "prat cc --file prompt.md" in output
    assert "prat cc --prompt=-leading-dash" in output


def test_agents_inventory_reports_aliases_and_capabilities(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["agents", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert [agent["name"] for agent in result["agents"]] == [agent.name for agent in AGENTS]
    kiro = next(agent for agent in result["agents"] if agent["name"] == "kiro")
    assert kiro["aliases"] == []
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
    assert (
        next(agent for agent in result["agents"] if agent["name"] == "claude")["capabilities"][
            "fast"
        ]
        is True
    )
    assert kiro["capabilities"]["fast"] is False


def test_agents_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "claude (cc): model, effort, fast, max_budget_usd, max_turns" in lines
    assert "kiro: model, effort" in lines
    assert "warp: model" in lines
    assert "qwen: model, max_turns" in lines
    assert "amp: none" in lines
    assert "kimi: model" in lines
    assert "vibe: max_budget_usd, max_turns" in lines


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
        "sources": [str(path)],
        "valid": True,
    }
    assert main(["config", "validate", "--config", str(path)]) == 0
    assert capsys.readouterr().out == f"Valid config: {path}\n"


def test_default_validate_explains_missing_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "validate"]) == 0
    assert "using built-in defaults" in capsys.readouterr().out


@pytest.mark.parametrize("command", [["profiles"], ["config", "validate"], ["doctor"]])
def test_management_merges_configs_and_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    global_path = init_config()
    local_path = tmp_path / ".pratfile"
    local_path.write_text('version=1\n[profiles.simple]\nagent="cc"\nmodel="local-model"\n')
    assert main([*command, "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["schema_version"] == 1
    assert captured.err == (
        f"prat: warning: {local_path}: profile 'simple' replaces the profile from {global_path}.\n"
    )
    if command == ["profiles"]:
        assert result["profiles"][0]["agent"] == "claude"
        assert result["profiles"][0]["options"]["model"] == "local-model"
    elif command == ["config", "validate"]:
        assert result == {
            "schema_version": 1,
            "path": str(local_path),
            "sources": [str(global_path), str(local_path)],
            "exists": True,
            "valid": True,
        }
    else:
        assert len(result["agents"]) == len(AGENTS)


def test_validate_text_reports_both_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    global_path = init_config()
    local_path = tmp_path / ".pratfile"
    local_path.write_text("version=1\n")
    assert main(["config", "validate"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"Valid config: {global_path}, {local_path}\n"
    assert captured.err == ""


def test_config_path_and_init_keep_global_target_with_local_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    local_path = tmp_path / ".pratfile"
    local_path.write_text("version=1\n")
    global_path = tmp_path / "config-home/pratfall/config.toml"
    assert main(["config", "path"]) == 0
    assert capsys.readouterr().out == f"{global_path}\n"
    assert main(["config", "init"]) == 0
    assert capsys.readouterr().out == f"Created {global_path}\n"
    assert global_path.is_file()
    assert local_path.read_text() == "version=1\n"


@pytest.mark.parametrize("source_layer", ["global", "local"])
@pytest.mark.parametrize("selector", ["cc", "work"])
def test_merged_native_validation_identifies_the_setting_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source_layer: str,
    selector: str,
) -> None:
    global_path = init_config()
    local_path = tmp_path / ".pratfile"
    source, other = (
        (global_path, local_path) if source_layer == "global" else (local_path, global_path)
    )
    other.write_text(
        'version=1\n[profiles.work]\nagent="cc"\n' if selector == "work" else "version=1\n"
    )
    source.write_text('version=1\n[defaults]\nnative_args=["--output-format", "text"]\n')
    command = ["config", "validate"] if selector == "work" else [selector, "prompt", "--dry-run"]
    assert main([*command, "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["message"].startswith(
        f"{source}: defaults.native_args (selector {selector!r}):"
    )


@pytest.mark.parametrize("source_layer", ["global", "local"])
@pytest.mark.parametrize("selector", ["gm", "work"])
def test_inherited_capability_error_identifies_the_setting_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source_layer: str,
    selector: str,
) -> None:
    global_path = init_config()
    local_path = tmp_path / ".pratfile"
    source, other = (
        (global_path, local_path) if source_layer == "global" else (local_path, global_path)
    )
    source.write_text("version=1\n[defaults]\nfast=true\n")
    other.write_text(
        'version=1\n[profiles.work]\nagent="gm"\n' if selector == "work" else "version=1\n"
    )
    assert main([selector, "prompt", "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert result["error"]["message"].startswith(
        f"{source}: defaults.fast (selector {selector!r}):"
    )


def test_cli_override_error_identifies_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    path = init_config()
    path.write_text("version=1\n[defaults]\nfast=true\n")
    assert main(["gm", "prompt", "--no-fast", "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["message"].startswith("command line: selector 'gm'.fast:")


def test_validation_reports_single_source_for_global_file_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = init_config()
    alias = tmp_path / "alias.toml"
    alias.symlink_to(path)
    assert main(["config", "validate", "--config", str(alias), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["sources"] == [str(alias)]


def test_local_profile_replaces_global_native_arguments_before_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    global_path = init_config()
    global_path.write_text(
        'version=1\n[profiles.work]\nagent="cc"\nnative_args=["--output-format", "text"]\n'
    )
    (tmp_path / ".pratfile").write_text('version=1\n[profiles.work]\nagent="cx"\n')
    assert main(["config", "validate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


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
    assert result["error"]["code"] == "invalid_arguments"
    assert f"{path}: defaults.native_args (selector 'bad')" in result["error"]["message"]
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
                "fast": None,
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
        "version": None,
        "version_error": None,
    }
    claude = next(agent for agent in result["agents"] if agent["agent"] == "claude")
    assert claude == {
        "agent": "claude",
        "executable": "claude",
        "path": None,
        "available": False,
        "version": None,
        "version_error": None,
    }
    assert main(["doctor", "--config", str(path)]) == 0
    assert f"codex: {wrapper}\n" in capsys.readouterr().out


def _version_config(tmp_path: Path, commands: dict[str, list[str]]) -> Path:
    path = tmp_path / "versions.toml"
    tables = ["version=1"]
    for agent, command in commands.items():
        tables.extend((f"[agents.{agent}]", f"command={json.dumps(command)}"))
    path.write_text("\n".join(tables), encoding="utf-8")
    return path


def test_doctor_versions_uses_literal_prefix_empty_stdin_and_stream_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", "")
    evidence = tmp_path / "evidence.json"
    fake = tmp_path / "version.py"
    fake.write_text(
        "import json,pathlib,sys\n"
        f"pathlib.Path({str(evidence)!r}).write_text(json.dumps([sys.argv[1:], len(sys.stdin.buffer.read())]))\n"
        "sys.stdout.write('  tool 1.2.3\\n')\n"
        "sys.stderr.buffer.write(b'ignored invalid: \\xff')\n",
        encoding="utf-8",
    )
    config = _version_config(
        tmp_path,
        {"codex": [sys.executable, str(fake), "two words", "$TOKEN"]},
    )
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    codex = next(agent for agent in result["agents"] if agent["agent"] == "codex")
    assert codex["version"] == "tool 1.2.3"
    assert codex["version_error"] is None
    assert json.loads(evidence.read_text(encoding="utf-8")) == [
        ["two words", "$TOKEN", "--version"],
        0,
    ]


def test_default_doctor_does_not_spawn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor started execution-only behavior")

    monkeypatch.setattr(doctor_module, "run", fail)
    monkeypatch.setattr(signal, "signal", fail)
    assert main(["doctor", "--json"]) == 0
    assert all(
        record["version"] is None for record in json.loads(capsys.readouterr().out)["agents"]
    )


def test_version_selection_strips_unicode_whitespace_before_stderr_fallback() -> None:
    fallback = _version_result(
        ProcessResult(RawCapture("\u2003".encode()), b" fallback 3.0 \n", 0, 0)
    )
    empty = _version_result(ProcessResult(RawCapture("\u2003".encode()), "\u2002".encode(), 0, 0))
    assert fallback == ("fallback 3.0", None)
    assert empty == (None, "Version probe returned no version text.")


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import sys;sys.stderr.write('fallback 2.0\\n')", None),
        ("import sys;sys.stdout.write('bad');sys.exit(9)", "status 9"),
        ("pass", "no version text"),
        ("import os;os.write(1,b'\\xff')", "not valid UTF-8"),
        ("import os;os.write(1,b'x'*65537)", "exceeded 65536 bytes"),
        ("import os;os.write(2,b'x'*65537)", "exceeded 65536 bytes"),
    ],
)
def test_doctor_versions_reports_diagnostic_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: str,
    message: str | None,
) -> None:
    monkeypatch.setenv("PATH", "")
    config = _version_config(tmp_path, {"codex": [sys.executable, "-c", code]})
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 0
    codex = next(
        agent
        for agent in json.loads(capsys.readouterr().out)["agents"]
        if agent["agent"] == "codex"
    )
    if message is None:
        assert codex["version"] == "fallback 2.0"
        assert codex["version_error"] is None
    else:
        assert codex["version"] is None
        assert message in codex["version_error"]


def test_doctor_version_timeout_is_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(doctor_module, "VERSION_TIMEOUT", 0.05)
    config = _version_config(
        tmp_path, {"codex": [sys.executable, "-c", "import time;time.sleep(30)"]}
    )
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 0
    codex = next(
        agent
        for agent in json.loads(capsys.readouterr().out)["agents"]
        if agent["agent"] == "codex"
    )
    assert codex["version"] is None
    assert "0.05 second timeout" in codex["version_error"]


def test_run_fast_flags_are_tri_state_and_conflicts_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "fast.toml"
    config.write_text("version=1\n[defaults]\nfast=false\n", encoding="utf-8")
    assert main(["cx", "prompt", "--config", str(config), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fast"] is False
    assert main(["cx", "prompt", "--fast", "--config", str(config), "--dry-run", "--json"]) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["fast"] is True
    assert 'service_tier="priority"' in enabled["argv"]
    assert main(["cx", "prompt", "--fast", "--no-fast", "--dry-run", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_arguments"


def test_run_rejects_fast_override_for_unsupported_agent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["gm", "prompt", "--no-fast", "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "does not support a fast-mode override" in result["error"]["message"]


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_doctor_version_interruption_during_probe_stops_and_restores_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chosen: signal.Signals,
) -> None:
    monkeypatch.setenv("PATH", "")
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    ready = tmp_path / "ready"
    later = tmp_path / "later"
    fake = tmp_path / "interrupt.py"
    fake.write_text(
        "import pathlib,sys,time\n"
        "mode=sys.argv[1]\n"
        f"ready=pathlib.Path({str(ready)!r});later=pathlib.Path({str(later)!r})\n"
        "(ready if mode == 'wait' else later).write_text(str(__import__('os').getpid()))\n"
        "time.sleep(30) if mode == 'wait' else print('later')\n",
        encoding="utf-8",
    )
    config = _version_config(
        tmp_path,
        {
            "codex": [sys.executable, str(fake), "wait"],
            "gemini": [sys.executable, str(fake), "later"],
        },
    )

    def interrupt() -> None:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        os.kill(os.getpid(), chosen)

    sender = threading.Thread(target=interrupt)
    sender.start()
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 128 + chosen
    sender.join()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 128 + chosen
    assert result["error"]["code"] == "interrupted"
    assert not later.exists()
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous
    pid = int(ready.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_doctor_version_interruption_during_discovery_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chosen: signal.Signals,
) -> None:
    config = _version_config(tmp_path, {"codex": [sys.executable, "-c", "print('later')"]})
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    discoveries = 0

    def interrupt_discovery(_executable: str) -> None:
        nonlocal discoveries
        discoveries += 1
        os.kill(os.getpid(), chosen)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor launched a version probe")

    monkeypatch.setattr(shutil, "which", interrupt_discovery)
    monkeypatch.setattr(doctor_module, "run", fail)
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 128 + chosen
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 128 + chosen
    assert discoveries == 1
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous


def test_doctor_version_interruption_before_first_inventory_item_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _version_config(tmp_path, {"codex": [sys.executable, "-c", "print('later')"]})

    def interrupt_before_inventory(
        loaded: Config, interruption: InterruptionState | None = None
    ) -> list[dict[str, object]]:
        assert interruption is not None
        os.kill(os.getpid(), signal.SIGINT)
        return _doctor_inventory(loaded, interruption)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor launched a version probe")

    monkeypatch.setattr(doctor_module, "_doctor_inventory", interrupt_before_inventory)
    monkeypatch.setattr(doctor_module, "run", fail)
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 130
    assert result["agents"] == []


def test_doctor_version_interruption_during_handler_install_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _version_config(tmp_path, {"codex": [sys.executable, "-c", "print('later')"]})
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    actual_signal = signal.signal
    installations = 0

    def interrupt_after_install(
        chosen: signal.Signals,
        handler: Callable[[int, FrameType | None], None] | int | None,
    ) -> Callable[[int, FrameType | None], None] | int | None:
        nonlocal installations
        prior = actual_signal(chosen, handler)
        installations += 1
        if installations == 1:
            os.kill(os.getpid(), signal.SIGINT)
        return prior

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor launched a version probe")

    monkeypatch.setattr(signal, "signal", interrupt_after_install)
    monkeypatch.setattr(doctor_module, "run", fail)
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 130
    assert result["agents"] == []
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_doctor_version_interruption_during_probe_selection_prevents_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chosen: signal.Signals,
) -> None:
    path = _version_config(tmp_path, {"codex": [sys.executable, "-c", "print('later')"]})
    loaded = load_config(path)
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }

    class InterruptingCommands(Mapping[str, tuple[str, ...]]):
        def __init__(self) -> None:
            self.codex_lookups = 0

        def __getitem__(self, key: str) -> tuple[str, ...]:
            command = loaded.commands[key]
            if key == "codex":
                self.codex_lookups += 1
                if self.codex_lookups == 2:
                    os.kill(os.getpid(), chosen)
            return command

        def __iter__(self) -> Iterator[str]:
            return iter(loaded.commands)

        def __len__(self) -> int:
            return len(loaded.commands)

    commands = InterruptingCommands()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor launched a version probe")

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(doctor_module, "run", fail)
    assert _doctor(replace(loaded, commands=commands), True, versions=True) == 128 + chosen
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 128 + chosen
    assert commands.codex_lookups == 2
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous


def test_doctor_version_interruption_during_result_preparation_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", "")
    config = _version_config(tmp_path, {"codex": [sys.executable, "-c", "print('1.0')"]})
    launches = 0
    line_calls = 0

    def counting_run(
        invocation: Invocation,
        cwd: Path,
        timeout: float,
        *,
        output_limits: OutputLimits,
    ) -> ProcessResult:
        nonlocal launches
        launches += 1
        return run_process(invocation, cwd, timeout, output_limits=output_limits)

    def interrupt_first_line(record: dict[str, object], *, versions: bool) -> str:
        nonlocal line_calls
        line_calls += 1
        if line_calls == 1:
            os.kill(os.getpid(), signal.SIGINT)
        return _doctor_line(record, versions=versions)

    monkeypatch.setattr(doctor_module, "run", counting_run)
    monkeypatch.setattr(doctor_module, "_doctor_line", interrupt_first_line)
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 130
    assert launches == 1


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_doctor_version_interruption_after_probe_prevents_next_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chosen: signal.Signals,
) -> None:
    monkeypatch.setenv("PATH", "")
    first = tmp_path / "first"
    later = tmp_path / "later"
    fake = tmp_path / "version.py"
    fake.write_text(
        "import pathlib,sys\n"
        f"first=pathlib.Path({str(first)!r});later=pathlib.Path({str(later)!r})\n"
        "(first if sys.argv[1] == 'first' else later).write_text('launched')\n"
        "print('1.0')\n",
        encoding="utf-8",
    )
    config = _version_config(
        tmp_path,
        {
            "codex": [sys.executable, str(fake), "first"],
            "gemini": [sys.executable, str(fake), "later"],
        },
    )
    previous = {
        candidate: signal.getsignal(candidate) for candidate in (signal.SIGINT, signal.SIGTERM)
    }
    launches = 0

    def interrupt_after_run(
        invocation: Invocation,
        cwd: Path,
        timeout: float,
        *,
        output_limits: OutputLimits,
    ) -> ProcessResult:
        nonlocal launches
        process = run_process(invocation, cwd, timeout, output_limits=output_limits)
        launches += 1
        os.kill(os.getpid(), chosen)
        return process

    monkeypatch.setattr(doctor_module, "run", interrupt_after_run)
    assert main(["doctor", "--versions", "--config", str(config), "--json"]) == 128 + chosen
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 128 + chosen
    assert launches == 1
    assert first.exists()
    assert not later.exists()
    assert {candidate: signal.getsignal(candidate) for candidate in previous} == previous


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


def test_new_agents_probe_independent_version_argv_with_empty_stdin(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    log = native_contract_config.parent / "native-calls.jsonl"
    assert main(["--config", str(native_contract_config), "doctor", "--json"]) == 0
    discovery = json.loads(capsys.readouterr().out)
    assert all(record["version"] is None for record in discovery["agents"])
    assert not log.exists()
    assert main(["--config", str(native_contract_config), "doctor", "--versions", "--json"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert {call["agent"]: call["argv"] for call in calls} == {
        "openhands": ["--version"],
        "warp": ["--version"],
        "iflow": ["--version"],
        "qwen": ["--version"],
        "amp": ["version"],
        "reasonix": ["--version"],
        "droid": ["--version"],
        "kimi": ["--version"],
        "vibe": ["--version"],
        "crush": ["--version"],
        "devin": ["--version"],
        "cortex": ["--version"],
        "grok": ["--version"],
    }
    assert len(calls) == 13
    for record in inventory["agents"]:
        if record["agent"] in {call["agent"] for call in calls}:
            assert record["version"] == f"{record['agent']} opaque version 1.0"
            assert record["version_error"] is None


@pytest.mark.parametrize("json_mode", [False, True])
def test_templates_listing_is_sorted_and_versioned(
    json_mode: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".pratfile"
    config.write_text(
        'version=1\n[templates.zed]\nprompt="last"\n[templates.alpha]\nprompt="Review\\n$input"\n',
        encoding="utf-8",
    )
    assert main(["templates", *(["--json"] if json_mode else [])]) == 0
    output = capsys.readouterr().out
    if json_mode:
        assert json.loads(output) == {
            "schema_version": 1,
            "templates": [
                {"name": "alpha", "prompt": "Review\n$input", "source": str(config)},
                {"name": "zed", "prompt": "last", "source": str(config)},
            ],
        }
    else:
        assert output == 'alpha: "Review\\n$input"\nzed: "last"\n'


def test_empty_templates_listing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["templates"]) == 0
    assert capsys.readouterr().out == "No templates configured.\n"
