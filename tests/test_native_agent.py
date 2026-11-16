import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import copilot_result
from pratfall.cli import main

AGENTS = ("claude", "copilot", "vibe")
PREFIXES = {
    "claude": ["-p", "--output-format", "json"],
    "copilot": ["--output-format=json"],
    "vibe": ["--prompt", "--output", "json"],
}


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("native", [["--agent=reviewer"], ["--agent", "auto-approve"]])
def test_native_agent_flag_reaches_the_agent_after_double_dash(
    agent: str,
    native: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
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
        f"pathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({native_output!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main([agent, "task", "--json", "--", *native]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    assert json.loads(log.read_text()) == [
        *PREFIXES[agent],
        *native,
        *(["--prompt=task"] if agent == "copilot" else []),
    ]


@pytest.mark.usefixtures("opencode_one_on_path")
def test_opencode_native_agent_stays_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["opencode", "task", "--dry-run", "--json", "--", "--agent=native"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["argv"] == ["opencode", "run", "--format", "json", "--agent=native"]


@pytest.mark.parametrize("arguments", [["--native-agent", "r"], ["--native-agent=r"]])
def test_removed_native_agent_flag_is_an_unknown_run_flag(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["cc", *arguments, "--dry-run", "--json", "hi"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["message"] == f"unrecognized argument: {arguments[0]}"


def test_removed_native_agent_setting_is_an_unknown_config_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('version = 1\n[profiles.rev]\nagent = "claude"\nnative_agent = "r"\n')
    assert main(["rev", "--config", str(config), "--dry-run", "--json", "hi"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["message"] == (
        f"{config}: profiles.rev: unknown field 'native_agent'; did you mean 'native_args'?"
    )
