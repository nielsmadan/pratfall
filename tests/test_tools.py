import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import copilot_result
from pratfall.cli import main

PREFIXES = {
    "claude": ["-p", "--output-format", "json"],
    "qwen": ["--output-format", "stream-json"],
    "copilot": ["--output-format=json"],
    "droid": ["exec", "--output-format", "json"],
    "vibe": ["--prompt", "--output", "json"],
    "pi": ["--print", "--mode", "json"],
}
NATIVE = {
    "claude": ["--tools=Read,Grep", "--disallowedTools", "Bash(git push *)"],
    "qwen": ["--core-tools=ShellTool", "--exclude-tools=Shell(rm *)"],
    "copilot": ["--available-tools", "Read", "--excluded-tools=Write"],
    "droid": ["--only-tools", "Read,Grep", "--add-tools=LS", "--remove-tools", "Execute"],
    "vibe": [r"--enabled-tools=re:^(read|search)[, ]", "--disabled-tools", "bash*"],
    "pi": ["--tools", "read,grep", "--exclude-tools", "bash"],
}
OUTPUT = {
    "copilot": "\n".join(
        (
            json.dumps({"type": "assistant.message", "data": {"messageId": "1", "content": "ok"}}),
            json.dumps(copilot_result()),
        )
    ),
    "vibe": '[{"type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]}]',
    "pi": "\n".join(
        json.dumps(event)
        for event in (
            {"type": "agent_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stopReason": "stop",
                },
            },
            {"type": "agent_end"},
            {"type": "agent_settled"},
        )
    ),
}
RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)


@pytest.mark.parametrize("agent", list(NATIVE))
def test_native_tool_flags_reach_the_agent_after_double_dash(
    agent: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "native.py"
    log = tmp_path / "argv.json"
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({OUTPUT.get(agent, RESULT)!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main([agent, "task", "--json", "--", *NATIVE[agent]]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    argv = json.loads(log.read_text())
    assert argv[: len(PREFIXES[agent])] == PREFIXES[agent]
    assert argv[len(PREFIXES[agent]) : len(PREFIXES[agent]) + len(NATIVE[agent])] == NATIVE[agent]


@pytest.mark.parametrize(
    "arguments", [["--tools", "Read"], ["--disabled-tools=Bash"], ["--tools=Read"]]
)
def test_removed_tool_flags_are_unknown_run_flags(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["cc", *arguments, "--dry-run", "--json", "hi"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["message"] == f"unrecognized argument: {arguments[0]}"


@pytest.mark.parametrize(
    ("table", "key"),
    [
        (
            '[profiles.ro]\nagent = "claude"\ntools = ["Read"]\n',
            "profiles.ro: unknown field 'tools'",
        ),
        ('[defaults]\ndisabled_tools = ["Bash"]\n', "defaults: unknown field 'disabled_tools'"),
    ],
)
def test_removed_tool_settings_are_unknown_config_fields(
    table: str, key: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f"version = 1\n{table}")
    assert main(["cc", "--config", str(config), "--dry-run", "--json", "hi"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["message"] == f"{config}: {key}"
