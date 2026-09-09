import io
import json
import sys
from pathlib import Path

import pytest

from pratfall.cli import PROMPT_LIMIT, main


class BrokenOutput:
    def write(self, _value: str) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        pass


def write_agent(
    tmp_path: Path,
    agent: str,
    code: str,
    *,
    profile: str | None = None,
    command: list[str] | None = None,
    settings: str = "",
) -> Path:
    script = tmp_path / f"{agent}_fake.py"
    script.write_text(code, encoding="utf-8")
    executable = command or [sys.executable, str(script)]
    profile_text = ""
    if profile is not None:
        profile_text = f'\n[profiles.{profile}]\nagent="{agent}"\n{settings}'
    config = tmp_path / f"{agent}.toml"
    config.write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps(executable)}\n{profile_text}",
        encoding="utf-8",
    )
    return config


CODEX_SUCCESS = """import json, os, sys
prompt = sys.stdin.buffer.read().decode()
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd()})
print(json.dumps({"type": "thread.started", "thread_id": "fixture"}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": answer}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "cached_input_tokens": 1, "output_tokens": 2}}))
print("native diagnostic", file=sys.stderr)
"""

CLAUDE_SUCCESS = """import json, sys
prompt = sys.stdin.buffer.read().decode()
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Claude: " + prompt, "usage": {"input_tokens": 2, "output_tokens": 3}}))
print("claude diagnostic", file=sys.stderr)
"""


def test_codex_dispatch_normalizes_output_and_supports_flags_around_selector(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS, profile="quick")
    exit_code = main(
        [
            "--json",
            "--model",
            "model-id",
            "quick",
            "--effort=high",
            "--cwd",
            str(work),
            "--prompt=-do work",
            "--config",
            str(config),
            "--",
            "--sandbox=read-only",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    result = json.loads(captured.out)
    answer = json.loads(result["output"])
    assert result == {
        "schema_version": 1,
        "agent": "codex",
        "profile": "quick",
        "model": "model-id",
        "status": "success",
        "output": result["output"],
        "exit_code": 0,
        "native_exit_code": 0,
        "duration_ms": result["duration_ms"],
        "usage": {
            "input_tokens": 3,
            "cached_input_tokens": 1,
            "cache_write_input_tokens": None,
            "output_tokens": 2,
            "reasoning_output_tokens": None,
        },
        "error": None,
    }
    assert answer["prompt"] == "-do work"
    assert answer["cwd"] == str(work)
    assert answer["argv"] == [
        "exec",
        "--json",
        "--model",
        "model-id",
        "-c",
        'model_reasoning_effort="high"',
        "--sandbox=read-only",
        "-",
    ]
    assert captured.err.startswith("prat: launching Codex\n")
    assert "native diagnostic\n" in captured.err
    assert "finished with status success" in captured.err


def test_claude_text_dispatch_prints_only_final_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "claude", CLAUDE_SUCCESS)
    assert main(["cc", "hello", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Claude: hello\n"
    assert "claude diagnostic" in captured.err
    assert '"type": "result"' not in captured.out


def test_claude_native_zero_exit_provider_failure_is_normalized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = """import json
print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "errors": ["Turn limit reached."], "usage": {"input_tokens": 5, "output_tokens": 2}}))
"""
    config = write_agent(tmp_path, "claude", failure)
    assert main(["cc", "prompt", "--config", str(config), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["native_exit_code"] == 0
    assert result["status"] == "error"
    assert result["usage"]["input_tokens"] == 5
    assert result["error"] == {"code": "provider_error", "message": "Turn limit reached."}


def test_stdin_prompt_is_read_once_as_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"multi\nline")))
    assert main(["cx", "-", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "multi\nline"


def test_prompt_option_can_pass_a_literal_dash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"not consumed")))
    assert main(["cx", "--prompt=-", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "-"


def test_dry_run_is_a_single_versioned_preview_and_does_not_launch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "raise SystemExit('must not launch')"
    config = write_agent(
        tmp_path,
        "codex",
        code,
        profile="safe",
        settings='model="configured"\nnative_args=["--sandbox", "workspace-write"]\n',
    )
    assert main(["--dry-run", "safe", "prompt", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema_version": 1,
        "dry_run": True,
        "agent": "codex",
        "profile": "safe",
        "model": "configured",
        "argv": [
            sys.executable,
            str(tmp_path / "codex_fake.py"),
            "exec",
            "--json",
            "--model",
            "configured",
            "--sandbox",
            "workspace-write",
            "-",
        ],
        "cwd": str(Path.cwd()),
        "timeout": 600.0,
        "stdin_bytes": 6,
    }
    assert main(["safe", "prompt", "--config", str(config), "--dry-run", "--json", "--"]) == 0
    replacement = json.loads(capsys.readouterr().out)
    assert "--sandbox" not in replacement["argv"]


def test_dispatch_rejects_invalid_native_arguments_in_an_unselected_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    with config.open("a", encoding="utf-8") as stream:
        stream.write('\n[profiles.bad]\nagent="claude"\nnative_args=["--output-format", "text"]\n')
    assert main(["cx", "prompt", "--config", str(config), "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert f"{config}: profiles.bad.native_args" in result["error"]["message"]


@pytest.mark.parametrize(
    ("code", "expected_exit", "expected_error"),
    [
        ("import sys;print('not json');sys.exit(7)", 7, "native_exit"),
        (
            'import json;print(json.dumps({"type":"turn.failed","error":{"message":"denied"}}))',
            1,
            "provider_error",
        ),
        ("import os;os.write(1,b'\\xff')", 1, "output_encoding"),
    ],
)
def test_runtime_failures_are_one_normalized_json_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    code: str,
    expected_exit: int,
    expected_error: str,
) -> None:
    config = write_agent(tmp_path, "codex", code)
    assert main(["cx", "prompt", "--config", str(config), "--json"]) == expected_exit
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["schema_version"] == 1
    assert result["status"] == "error"
    assert result["exit_code"] == expected_exit
    assert result["error"]["code"] == expected_error
    assert captured.out.count("\n") == 1


def test_missing_and_nonexecutable_agents_map_to_shell_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = write_agent(
        tmp_path,
        "codex",
        "",
        command=[str(tmp_path / "missing")],
    )
    assert main(["cx", "prompt", "--config", str(missing), "--json"]) == 127
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "executable_not_found"
    blocked_file = tmp_path / "blocked"
    blocked_file.write_text("plain", encoding="utf-8")
    blocked = write_agent(tmp_path, "claude", "", command=[str(blocked_file)])
    assert main(["cc", "prompt", "--config", str(blocked), "--json"]) == 126
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "executable_not_executable"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["cx"], "exactly one prompt"),
        (["cx", "one", "two"], "exactly one prompt"),
        (["cx", "--prompt", "text"], "--prompt=TEXT"),
        (["cx", ""], "must not be empty"),
        (["cx", "a\0b"], "NUL"),
        (["--timeout=nan", "cx", "prompt"], "finite positive"),
        (["--max-turns=1.5", "cc", "prompt"], "positive integer"),
        (["cx", "prompt", "--cwd=/missing-prat-directory"], "not a directory"),
    ],
)
def test_run_argument_errors_use_full_json_contract_when_requested(
    arguments: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--json", *arguments]) == 2
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {
        "schema_version",
        "agent",
        "profile",
        "model",
        "status",
        "output",
        "exit_code",
        "native_exit_code",
        "duration_ms",
        "usage",
        "error",
    }
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_arguments"
    assert message in result["error"]["message"]


def test_json_token_consumed_as_an_option_value_is_not_an_output_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["cx", "prompt", "--model", "--json", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("command: ")
    assert not captured.out.startswith("{")


def test_prompt_byte_limit_and_invalid_stdin_utf8(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exact = "é" * (PROMPT_LIMIT // 2)
    assert main(["cx", exact, "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["stdin_bytes"] == PROMPT_LIMIT
    assert main(["cx", exact + "é", "--dry-run", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_arguments"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff")))
    assert main(["cx", "-", "--dry-run", "--json"]) == 2
    assert "not valid UTF-8" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_unimplemented_adapter_fails_before_launch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["gm", "prompt", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["agent"] == "gemini"
    assert result["error"]["code"] == "unsupported_agent"


def test_broken_output_pipe_returns_one_after_child_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "finished"
    code = f"from pathlib import Path;Path({str(marker)!r}).write_text('done');" + CODEX_SUCCESS
    config = write_agent(tmp_path, "codex", code)
    monkeypatch.setattr(sys, "stdout", BrokenOutput())
    assert main(["cx", "prompt", "--config", str(config)]) == 1
    assert marker.read_text(encoding="utf-8") == "done"
