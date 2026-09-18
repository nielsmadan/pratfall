import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import copilot_result
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, option_origins, parse_options, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options

AGENTS = ("claude", "copilot", "vibe")
PREFIXES = {
    "claude": ["-p", "--output-format", "json"],
    "copilot": ["--output-format=json"],
    "vibe": ["--prompt", "--output", "json"],
}


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("name", ["reviewer", "-literal /审查,=name ", "auto-approve"])
def test_native_agent_fake_receives_exact_literal_argv(
    agent: str, name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    log = tmp_path / "argv.json"
    native_output = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    if agent == "copilot":
        native_output = (
            json.dumps({"type": "assistant.message", "data": {"messageId": "1", "content": "ok"}})
            + "\n"
            + json.dumps(copilot_result())
        )
    elif agent == "vibe":
        native_output = (
            '[{"type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]}]'
        )
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps([sys.argv[1:], sys.stdin.read()]))\n"
        f"print({native_output!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main([f"--native-agent={name}", agent, "task", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    argv, stdin = json.loads(log.read_text())
    assert argv == [
        *PREFIXES[agent],
        f"--agent={name}",
        *(["--prompt=task"] if agent == "copilot" else []),
    ]
    assert stdin == ("" if agent == "copilot" else "task")


def test_native_agent_four_layer_precedence_and_provenance(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\nnative_agent="global"\n')
    global_config = load_config()
    assert resolve_profile(global_config, "claude").options.native_agent == "global"
    assert option_origins(global_config, "claude")["native_agent"].label == (
        f"{global_path}: defaults.native_agent (selector 'claude')"
    )
    local = tmp_path / ".pratfile"
    local.write_text(
        'version=1\n[defaults]\nnative_agent="local"\n'
        '[profiles.work]\nagent="claude"\nnative_agent="profile"\n'
    )
    config = load_config()
    assert resolve_profile(config, "claude").options.native_agent == "local"
    assert option_origins(config, "claude")["native_agent"].label == (
        f"{local}: defaults.native_agent (selector 'claude')"
    )
    assert resolve_profile(config, "work").options.native_agent == "profile"
    assert option_origins(config, "work")["native_agent"].label == (
        f"{local}: profiles.work.native_agent"
    )
    cli = Options(native_agent="cli")
    resolved = resolve_profile(config, "work", cli)
    assert (resolved.agent.name, resolved.profile, resolved.options.native_agent) == (
        "claude",
        "work",
        "cli",
    )
    origin = option_origins(config, "work", cli)["native_agent"]
    assert origin.label == "command line: selector 'work'.native_agent"
    assert origin.code == "invalid_arguments"


@pytest.mark.parametrize("agent", AGENTS)
def test_native_agent_profile_and_cli_preview(
    agent: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text(
        f'version=1\n[profiles.review]\nagent="{agent}"\nnative_agent="profile"\n'
    )
    for flags, name in (([], "profile"), (["--native-agent", "cli"], "cli")):
        assert main(["review", "task", *flags, "--json", "--dry-run"]) == 0
        preview = json.loads(capsys.readouterr().out)
        assert preview["argv"] == [
            agent,
            *PREFIXES[agent],
            f"--agent={name}",
            *(["--prompt=task"] if agent == "copilot" else []),
        ]


@pytest.mark.parametrize("value", [None, True, 1, [], "", " \t", "a\0b", "\ud800"])
def test_native_agent_config_validation(value: object) -> None:
    with pytest.raises(PratError, match=r"defaults\.native_agent") as caught:
        parse_options({"native_agent": value}, "config.toml: defaults")
    assert caught.value.code == "invalid_config"


@pytest.mark.parametrize("value", ["", " \t", "a\0b", "\ud800"])
def test_invalid_native_agent_rejected_before_input_or_editor(
    value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt/editor must not be reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main(["claude", "--native-agent", value, "--edit", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "--native-agent" in error["message"]


@pytest.mark.parametrize("agent", ["codex", "opencode", "qwen"])
def test_unsupported_native_agent_rejected_before_input_or_editor(
    agent: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt/editor must not be reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main([agent, "--native-agent=reviewer", "--file=missing", "--edit", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "does not support native agent selection" in error["message"]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("native", [["--agent=native"], ["--agent", "native"]])
def test_native_agent_collision_rejected_before_input(
    agent: str,
    native: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt must not be acquired")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main([agent, "--native-agent=public", "--json", "--", *native]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "controlled by prat" in error["message"]


@pytest.mark.parametrize("agent", ["claude", "copilot", "opencode"])
def test_native_only_agent_stays_accepted(agent: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([agent, "task", "--dry-run", "--json", "--", "--agent=native"]) == 0
    preview = json.loads(capsys.readouterr().out)
    prefix = ["run", "--format", "json"] if agent == "opencode" else PREFIXES[agent]
    assert preview["argv"] == [
        agent,
        *prefix,
        "--agent=native",
        *(["--prompt=task"] if agent == "copilot" else []),
    ]


def test_vibe_native_agent_remains_reserved(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vibe", "task", "--json", "--", "--agent=ask"]) == 2
    assert "controlled by prat" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_native_agent_inventory_and_profile_listing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {r["name"] for r in records if r["capabilities"]["native_agent"]} == set(AGENTS)
    (tmp_path / ".pratfile").write_text(
        'version=1\n[profiles.review]\nagent="claude"\nnative_agent=" native name "\n'
    )
    assert main(["profiles", "--json"]) == 0
    profile = json.loads(capsys.readouterr().out)["profiles"][0]
    assert profile["agent"] == "claude"
    assert profile["name"] == "review"
    assert profile["options"]["native_agent"] == " native name "
    assert main(["profiles"]) == 0
    assert 'native_agent=" native name "' in capsys.readouterr().out


def test_native_agent_defaults_reject_unsupported_unused_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(
        'version=1\n[defaults]\nnative_agent="reviewer"\n[profiles.unused]\nagent="codex"\n'
    )
    assert main(["claude", "task", "--dry-run", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{path}: defaults.native_agent" in error["message"]


def test_native_agent_config_collision_reports_native_argument_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(
        'version=1\n[defaults]\nnative_agent="reviewer"\n'
        '[profiles.work]\nagent="claude"\nnative_args=["--agent=other"]\n'
    )
    assert main(["config", "validate", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert f"{path}: profiles.work.native_args" in error["message"]
    assert "controlled by prat" in error["message"]
