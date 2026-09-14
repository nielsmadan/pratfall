import fcntl
import io
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import suppress
from itertools import pairwise
from pathlib import Path

import pytest

from pratfall import runner as runner_module
from pratfall.cli import dispatch as dispatch_module
from pratfall.cli import main
from pratfall.config import init_config
from pratfall.models import ConsumedCapture, DecodedOutput, RawCapture, ResultError, Usage
from pratfall.prompt_input import PROMPT_LIMIT
from pratfall.runner import ProcessResult


class BrokenOutput:
    def write(self, _value: str) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        pass


class UnreadInput:
    buffer: "UnreadInput"

    def __init__(self, *, terminal: bool = False) -> None:
        self.buffer = self
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal

    def fileno(self) -> int:
        raise OSError("no descriptor")

    def read(self, _size: int) -> bytes:
        raise AssertionError("stdin must not be read")


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

NEW_ADAPTER_FIXTURES = {
    "gemini": """import json, sys
prompt = next(arg for arg in sys.argv if arg.startswith("--prompt=")).split("=", 1)[1]
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"response": answer}))
""",
    "antigravity": """import json, sys
prompt = json.loads(sys.stdin.read())["message"]["content"]
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"event": "init", "init": {"cwd": "/work"}}))
print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": answer, "usage": {"input_tokens": 3, "output_tokens": 2, "thinking_tokens": 1, "cache_read_tokens": 1, "total_tokens": 5}}}))
""",
    "copilot": """import json, sys
prompt = next(arg for arg in sys.argv if arg.startswith("--prompt=")).split("=", 1)[1]
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"type": "assistant.message", "data": {"messageId": "answer", "content": answer}}))
print(json.dumps({"type": "result", "timestamp": "2026-09-09T12:00:00Z", "sessionId": "id", "exitCode": 0, "usage": {"premiumRequests": 1, "totalApiDurationMs": 2, "sessionDurationMs": 3, "codeChanges": {"linesAdded": 0, "linesRemoved": 0, "filesModified": 0}}}))
""",
    "cursor": """import json, sys
prompt = sys.argv[sys.argv.index("--") + 1]
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "session_id": "id"}))
""",
    "opencode": """import json, sys
prompt = sys.stdin.read()
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"type": "text", "part": {"id": "answer", "type": "text", "text": answer, "time": {"end": 1}}}))
print(json.dumps({"type": "step_finish", "part": {"id": "step", "type": "step-finish", "reason": "stop", "cost": 0.01, "tokens": {"total": 5, "input": 3, "output": 2, "reasoning": 0, "cache": {"read": 1, "write": 0}}}}))
""",
    "kiro": """import json, sys
prompt = sys.argv[sys.argv.index("--") + 1]
print(json.dumps({"argv": sys.argv[1:], "prompt": prompt}))
""",
    "openclaw": """import json, sys
prompt = sys.stdin.read()
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"ok": True, "status": "ok", "final": answer, "payloads": [{"text": answer}], "usage": {"input": 3, "output": 2, "total": 5}}))
""",
    "hermes": """import json, sys
prompt = sys.stdin.read()
print(json.dumps({"argv": sys.argv[1:], "prompt": prompt}))
""",
}


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
        "reported_models": None,
        "cost_usd": None,
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


SURROGATE_ANSWERS = {
    "claude": '{"type":"result","subtype":"success","is_error":false,'
    '"result":"before\\ud800after","usage":{"input_tokens":1,"output_tokens":1}}',
    "cursor": '{"type":"result","subtype":"success","is_error":false,'
    '"result":"before\\ud800after"}',
    "droid": '{"type":"result","subtype":"success","is_error":false,"result":"before\\ud800after"}',
    "gemini": '{"response":"before\\ud800after"}',
    "openclaw": '{"ok":true,"status":"ok","final":"before\\ud800after","payloads":[]}',
    "reasonix": '{"type":"result","subtype":"success","is_error":false,'
    '"result":"before\\ud800after"}',
    "vibe": '[{"type":"message","role":"assistant",'
    '"content":[{"type":"text","text":"before\\ud800after"}]}]',
}


def surrogate_agent(payload: str) -> str:
    return f"import sys\nsys.stdin.buffer.read()\nsys.stdout.write({payload!r})\n"


@pytest.mark.parametrize("agent", sorted(SURROGATE_ANSWERS))
@pytest.mark.parametrize("mode", [[], ["--json"]], ids=["text", "json"])
def test_unencodable_answer_is_normalized_instead_of_crashing(
    agent: str, mode: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, agent, surrogate_agent(SURROGATE_ANSWERS[agent]))
    assert main([agent, "prompt", "--config", str(config), *mode]) == 1
    captured = capsys.readouterr()
    if mode:
        assert json.loads(captured.out)["output"] == ""
        assert json.loads(captured.out)["error"] == {
            "code": "output_encoding",
            "message": "Agent output contains invalid Unicode text.",
        }
    else:
        assert captured.out == ""
        assert "prat: Agent output contains invalid Unicode text.\n" in captured.err


@pytest.mark.parametrize("local_name", [".pratfile", "custom.toml"])
@pytest.mark.parametrize("mode", [[], ["--json"], ["--json", "--progress"]])
def test_run_uses_local_profile_and_global_wrapper_from_invocation_directory(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    local_name: str,
    mode: list[str],
) -> None:
    monkeypatch.setenv("PATH", "")
    fake_config = write_agent(tmp_path, "codex", CODEX_SUCCESS, profile="work")
    global_path = init_config()
    global_path.write_text(fake_config.read_text())
    local_path = tmp_path / local_name
    local_path.write_text('version=1\n[profiles.work]\nagent="cx"\nmodel="local-model"\n')
    child = tmp_path / "child"
    child.mkdir()
    (child / ".pratfile").write_text("invalid child config")
    config_flags = [] if local_name == ".pratfile" else ["--config", local_name]
    if config_flags:
        (tmp_path / ".pratfile").write_text("invalid automatic config")
    assert main(["work", "prompt", "--cwd", str(child), *config_flags, *mode]) == 0
    captured = capfd.readouterr()
    if "--json" in mode:
        result = json.loads(captured.out)
        assert result["status"] == "success"
        assert result["model"] == "local-model"
        answer = json.loads(result["output"])
    else:
        answer = json.loads(captured.out)
    assert answer == {
        "argv": ["exec", "--json", "--model", "local-model", "-"],
        "prompt": "prompt",
        "cwd": str(child),
    }
    warning = (
        f"prat: warning: {local_path}: profile 'work' replaces the profile from {global_path}.\n"
    )
    assert captured.err.count(warning) == 1
    assert "native diagnostic\n" in captured.err


@pytest.mark.parametrize("flags", [[], ["--progress"]])
def test_config_warning_failure_returns_json_error_before_reading_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    init_config()
    (tmp_path / ".pratfile").write_text('version=1\n[profiles.simple]\nagent="cx"\n')
    monkeypatch.setattr(sys, "stderr", BrokenOutput())
    monkeypatch.setattr(sys, "stdin", UnreadInput())
    assert main(["simple", "-", "--json", *flags]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "output_io_error"
    assert "Cannot write config warnings" in result["error"]["message"]


def test_invalid_local_config_fails_before_reading_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text("version=2\n")
    monkeypatch.setattr(sys, "stdin", UnreadInput())
    assert main(["cx", "-", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_config"
    assert ".pratfile: version must be the integer 1" in result["error"]["message"]


def test_claude_native_zero_exit_provider_failure_is_normalized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = """import json
print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "errors": ["Turn limit reached."], "usage": {"input_tokens": 5, "output_tokens": 2}, "modelUsage": {"native-primary": {}, "native-helper": {}}, "total_cost_usd": 0}))
"""
    config = write_agent(tmp_path, "claude", failure)
    assert main(["cc", "prompt", "--model", "requested", "--config", str(config), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["native_exit_code"] == 0
    assert result["status"] == "error"
    assert result["model"] == "requested"
    assert result["reported_models"] == ["native-primary", "native-helper"]
    assert result["cost_usd"] == 0
    assert result["usage"]["input_tokens"] == 5
    assert result["error"] == {"code": "provider_error", "message": "Turn limit reached."}


@pytest.mark.parametrize("agent", NEW_ADAPTER_FIXTURES)
def test_new_adapter_fake_cli_dispatches_real_protocol(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent: str,
) -> None:
    config = write_agent(tmp_path, agent, NEW_ADAPTER_FIXTURES[agent])
    assert main([agent, "--prompt=-exact prompt", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    answer = json.loads(result["output"])
    assert result["agent"] == agent
    assert result["status"] == "success"
    assert result["native_exit_code"] == 0
    assert answer["prompt"] == "-exact prompt"


def test_kiro_fake_cli_dispatches_model_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "kiro", NEW_ADAPTER_FIXTURES["kiro"])
    assert (
        main(
            [
                "kiro",
                "prompt",
                "--model",
                "claude-sonnet-4",
                "--config",
                str(config),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    answer = json.loads(result["output"])
    assert result["model"] == "claude-sonnet-4"
    assert answer["argv"] == [
        "chat",
        "--no-interactive",
        "--wrap",
        "never",
        "--model",
        "claude-sonnet-4",
        "--",
        "prompt",
    ]


def test_copilot_profile_transmits_optional_variadic_native_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    native_args = [
        "--available-tools",
        "--allow-tool",
        "shell(git status)",
        "write",
        "--deny-url=https://example.com",
        "--no-color",
    ]
    config = write_agent(
        tmp_path,
        "copilot",
        NEW_ADAPTER_FIXTURES["copilot"],
        profile="permissions",
        settings=f"native_args={json.dumps(native_args)}\n",
    )
    assert main(["permissions", "prompt", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["argv"] == [
        "--output-format=json",
        *native_args,
        "--prompt=prompt",
    ]


def test_gemini_explicit_provider_timeout_maps_to_124(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = """import json
print(json.dumps({"response": "partial", "error": {"type": "TimeoutError", "message": "provider timed out"}}))
"""
    config = write_agent(tmp_path, "gemini", fixture)
    assert main(["gm", "prompt", "--config", str(config), "--json"]) == 124
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["output"] == "partial"
    assert result["error"] == {"code": "timeout", "message": "provider timed out"}


def test_openclaw_native_timeout_maps_to_124_and_preserves_partial_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = """import json, sys
print(json.dumps({"ok": False, "status": "timeout", "final": "partial", "payloads": [{"text": "partial"}], "error": {"message": "native deadline", "kind": "timeout"}}))
sys.exit(2)
"""
    config = write_agent(tmp_path, "openclaw", fixture)
    assert main(["claw", "prompt", "--config", str(config), "--json"]) == 124
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["native_exit_code"] == 2
    assert result["output"] == "partial"
    assert result["error"] == {"code": "timeout", "message": "native deadline"}


def test_codex_outer_timeout_preserves_completed_message_and_cleans_fake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path = tmp_path / "fake.lock"
    ready_path = tmp_path / "fake.ready"
    pid_path = tmp_path / "fake.pid"
    fixture = f"""import fcntl, json, os, time
from pathlib import Path
lock = open({str(lock_path)!r}, "wb")
fcntl.flock(lock, fcntl.LOCK_EX)
Path({str(pid_path)!r}).write_text(str(os.getpid()))
Path({str(ready_path)!r}).write_text("ready")
print(json.dumps({{"type": "turn.started"}}), flush=True)
print(json.dumps({{"type": "item.completed", "item": {{"id": "answer", "type": "agent_message", "text": "partial"}}}}), flush=True)
time.sleep(30)
"""
    config = write_agent(tmp_path, "codex", fixture)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cleaned = False
    try:
        assert main(["cx", "prompt", "--timeout", "1", "--config", str(config), "--json"]) == 124
        result = json.loads(capsys.readouterr().out)
        assert ready_path.read_text(encoding="utf-8") == "ready"
        assert result["status"] == "timeout"
        assert result["output"] == "partial"
        assert result["error"]["code"] == "timeout"
        with lock_path.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                cleaned = False
            else:
                cleaned = True
                fcntl.flock(stream, fcntl.LOCK_UN)
        assert cleaned
    finally:
        if not cleaned and pid_path.exists():
            pid = int(pid_path.read_text(encoding="utf-8"))
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)


@pytest.mark.parametrize(
    ("agent", "native_argument"),
    [
        ("gemini", "--output-format=text"),
        ("antigravity", "--input-format=text"),
        ("copilot", "--prompt=native"),
        ("cursor", "--workspace=/elsewhere"),
        ("opencode", "--variant=native"),
        ("kiro", "--wrap=always"),
        ("openclaw", "--state-dir=/tmp/state"),
        ("hermes", "--query=native"),
    ],
)
def test_all_profiles_validate_new_adapter_native_arguments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent: str,
    native_argument: str,
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            f'\n[profiles.bad]\nagent="{agent}"\nnative_args=[{json.dumps(native_argument)}]\n'
        )
    assert main(["cx", "prompt", "--config", str(config), "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "profiles.bad.native_args" in result["error"]["message"]
    assert "controlled by prat" in result["error"]["message"]


def test_config_validation_checks_openclaw_fallback_model_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            '\n[profiles.bad]\nagent="openclaw"\nnative_args=["--fallback", "provider/backup"]\n'
        )
    assert main(["config", "validate", "--config", str(config), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "profiles.bad.native_args" in result["error"]["message"]
    assert "fallback requires an explicit model" in result["error"]["message"]


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


def test_redirected_stdin_is_the_implicit_prompt_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO("implicit café\n".encode())))
    assert main(["cx", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "implicit café\n"


@pytest.mark.parametrize("form", ["short", "long", "equals"])
def test_file_prompt_forms_preserve_unicode_and_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    form: str,
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    prompt_file = tmp_path / "- prompt file.md"
    prompt_file.write_bytes("first\n雪\n".encode())
    monkeypatch.chdir(tmp_path)
    if form == "short":
        source = ["-f", prompt_file.name]
    elif form == "long":
        source = ["--file", prompt_file.name]
    else:
        source = [f"--file={prompt_file.name}"]
    arguments = [*source, "cx"] if form == "short" else ["cx", *source]
    assert main([*arguments, "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "first\n雪\n"


def test_file_dash_reads_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"file dash\n")))
    assert main(["cx", "--file", "-", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "file dash\n"


def test_explicit_file_does_not_read_incidental_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("chosen", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", UnreadInput())
    assert main(["cx", "--file", str(prompt_file), "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "chosen"


def test_relative_prompt_file_uses_invocation_directory_not_run_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invocation = tmp_path / "invocation"
    run_cwd = tmp_path / "work"
    invocation.mkdir()
    run_cwd.mkdir()
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    (invocation / "prompt.md").write_text("from invocation", encoding="utf-8")
    monkeypatch.chdir(invocation)
    assert (
        main(
            [
                "cx",
                "--file",
                "prompt.md",
                "--cwd",
                str(run_cwd),
                "--config",
                str(config),
                "--json",
            ]
        )
        == 0
    )
    answer = json.loads(json.loads(capsys.readouterr().out)["output"])
    assert answer == {"argv": answer["argv"], "prompt": "from invocation", "cwd": str(run_cwd)}


def test_symlink_to_regular_prompt_file_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    target = tmp_path / "target.md"
    target.write_text("through symlink", encoding="utf-8")
    prompt_file = tmp_path / "prompt link.md"
    prompt_file.symlink_to(target)
    assert main(["cx", "-f", str(prompt_file), "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(result["output"])["prompt"] == "through symlink"


def test_valid_file_is_fully_validated_by_dry_run_without_launching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "launched"
    config = write_agent(
        tmp_path, "codex", f"from pathlib import Path;Path({str(marker)!r}).touch()"
    )
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("dry café\n", encoding="utf-8")
    assert (
        main(["cx", "--file", str(prompt_file), "--config", str(config), "--dry-run", "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["stdin_bytes"] == len("dry café\n".encode())
    assert not marker.exists()


def test_native_file_after_separator_is_not_a_prompt_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = """import json, sys
prompt = sys.stdin.read()
answer = json.dumps({"argv": sys.argv[1:], "prompt": prompt})
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""
    config = write_agent(tmp_path, "claude", fixture)
    assert (
        main(
            [
                "cc",
                "wrapper prompt",
                "--config",
                str(config),
                "--json",
                "--",
                "--file",
                "native path.md",
            ]
        )
        == 0
    )
    answer = json.loads(json.loads(capsys.readouterr().out)["output"])
    assert answer["prompt"] == "wrapper prompt"
    assert answer["argv"][-2:] == ["--file", "native path.md"]


@pytest.mark.parametrize(
    "sources",
    [
        ["inline", "--file", "prompt.md"],
        ["--file", "one.md", "--file", "two.md"],
        ["inline", "--prompt=other"],
        ["-", "--file=-"],
    ],
)
def test_prompt_source_conflicts_are_normalized_without_opening_input(
    sources: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", UnreadInput())
    assert main(["cx", *sources, "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "Provide exactly one prompt source.",
    }


@pytest.mark.parametrize("source", [[], ["-"], ["--file", "-"]])
@pytest.mark.parametrize(
    "case",
    [
        ("codex", ["--cwd=missing"], "--cwd is not a directory"),
        ("codex", ["--", "--model", "x"], "this option is controlled by prat"),
        ("openclaw", ["--", "--fallback", "x"], "requires an explicit model override"),
    ],
)
def test_invalid_run_options_are_rejected_before_reading_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: list[str],
    case: tuple[str, list[str], str],
) -> None:
    agent, options, message = case
    marker = tmp_path / "launched"
    config = write_agent(tmp_path, agent, f"from pathlib import Path;Path({str(marker)!r}).touch()")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", UnreadInput())
    assert main([agent, *source, "--config", str(config), "--json", *options]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert message in result["error"]["message"]
    assert result["native_exit_code"] is None
    assert not marker.exists()


def test_terminal_without_prompt_is_an_actionable_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", UnreadInput(terminal=True))
    assert main(["cx", "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "--file PATH" in result["error"]["message"]
    assert "redirected standard input" in result["error"]["message"]


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"", "empty or whitespace-only"),
        (" \n\t\u2003".encode(), "empty or whitespace-only"),
        (b"nul\0byte", "NUL"),
        (b"\xff", "not valid UTF-8"),
        (b"x" * (PROMPT_LIMIT + 1), "byte limit"),
    ],
)
def test_invalid_file_content_is_normalized_and_dry_run_does_not_launch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: bytes,
    message: str,
) -> None:
    marker = tmp_path / "launched"
    config = write_agent(
        tmp_path, "codex", f"from pathlib import Path;Path({str(marker)!r}).touch()"
    )
    prompt_file = tmp_path / "prompt.bin"
    prompt_file.write_bytes(contents)
    assert (
        main(["cx", "--file", str(prompt_file), "--config", str(config), "--dry-run", "--json"])
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert message in result["error"]["message"]
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_invalid_named_file_is_rejected_without_launching(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    marker = tmp_path / "launched"
    config = write_agent(
        tmp_path, "codex", f"from pathlib import Path;Path({str(marker)!r}).touch()"
    )
    prompt_file = tmp_path / kind
    if kind == "directory":
        prompt_file.mkdir()
    elif kind == "fifo":
        os.mkfifo(prompt_file)
    assert main(["cx", "-f", str(prompt_file), "--config", str(config), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "prompt file" in result["error"]["message"].lower()
    assert not marker.exists()


def test_unreadable_prompt_file_is_normalized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt_file = tmp_path / "unreadable.md"
    prompt_file.write_text("secret", encoding="utf-8")
    prompt_file.chmod(0)
    try:
        assert main(["cx", "--file", str(prompt_file), "--dry-run", "--json"]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["error"]["code"] == "invalid_arguments"
        assert "Cannot open prompt file" in result["error"]["message"]
    finally:
        prompt_file.chmod(0o600)


def test_closed_stdin_is_a_normalized_input_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stream = io.BytesIO(b"prompt")
    stream.close()
    monkeypatch.setattr(sys, "stdin", stream)
    assert main(["cx", "-", "--dry-run", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert "Cannot read standard input" in result["error"]["message"]


def test_stdin_closed_before_startup_is_normalized_and_explicit_sources_work(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "native-launched"
    config = write_agent(
        tmp_path,
        "codex",
        f"from pathlib import Path;Path({str(marker)!r}).touch()\n{CODEX_SUCCESS}",
    )
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("file prompt", encoding="utf-8")
    base = [
        "/bin/sh",
        "-c",
        'exec 0<&-; exec "$@"',
        "closed-stdin-probe",
        sys.executable,
        "-m",
        "pratfall",
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }

    for source in ([], ["-"], ["--file", "-"]):
        completed = subprocess.run(
            [*base, "cx", *source, "--config", str(config), "--json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )
        result = json.loads(completed.stdout)
        assert completed.returncode == result["exit_code"] == 2
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_arguments"
        assert "Cannot read standard input" in result["error"]["message"]
        assert completed.stdout.count(b"\n") == 1
        assert b"Traceback" not in completed.stderr
        assert not marker.exists()

    for source, expected in (
        (["inline prompt"], "inline prompt"),
        (["--file", str(prompt_file)], "file prompt"),
    ):
        completed = subprocess.run(
            [*base, "cx", *source, "--config", str(config), "--json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )
        result = json.loads(completed.stdout)
        assert completed.returncode == result["exit_code"] == 0
        assert result["status"] == "success"
        assert json.loads(result["output"])["prompt"] == expected
        assert marker.exists()
        marker.unlink()


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_signal_while_reading_implicit_stdin_is_normalized_without_spawning(
    tmp_path: Path, chosen: signal.Signals
) -> None:
    marker = tmp_path / "launched"
    ready = tmp_path / "input-handlers-ready"
    config = write_agent(
        tmp_path, "codex", f"from pathlib import Path;Path({str(marker)!r}).touch()"
    )
    bootstrap = """
import sys
from contextlib import contextmanager
from pathlib import Path

from pratfall import prompt_input
from pratfall.cli import main

original_handlers = prompt_input._input_signal_handlers

@contextmanager
def ready_handlers(state):
    with original_handlers(state):
        Path(sys.argv[1]).touch()
        yield

prompt_input._input_signal_handlers = ready_handlers
raise SystemExit(main(sys.argv[2:]))
"""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            bootstrap,
            str(ready),
            "cx",
            "--config",
            str(config),
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child did not install input signal handlers"
        process.send_signal(chosen)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
    assert process.returncode == 128 + chosen
    result = json.loads(stdout)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 128 + chosen
    assert result["error"] == {
        "code": "interrupted",
        "message": f"Interrupted by signal {chosen}.",
    }
    assert stdout.count(b"\n") == 1
    assert stderr == b""
    assert not marker.exists()


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
        "fast": None,
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
    assert result["error"]["code"] == "invalid_arguments"
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


def test_progress_is_live_bounded_and_preserves_one_final_json_object(tmp_path: Path) -> None:
    ready = tmp_path / "progress-ready"
    release = tmp_path / "progress-release"
    fixture = f"""import json,time
print(json.dumps({{"type":"item.completed","item":{{"id":"a","type":"agent_message","text":"done"}}}}), flush=True)
open({str(ready)!r}, "w").write("ready")
while not __import__("os").path.exists({str(release)!r}): time.sleep(0.01)
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}}}), flush=True)
"""
    config = write_agent(tmp_path, "codex", fixture)
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pratfall",
            "cx",
            "prompt",
            "--progress",
            "--json",
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stderr_data = bytearray()
    selected = selectors.DefaultSelector()
    try:
        assert process.stderr is not None
        selected.register(process.stderr, selectors.EVENT_READ)
        deadline = time.monotonic() + 5
        while b"answering" not in stderr_data and time.monotonic() < deadline:
            if selected.select(0.1):
                stderr_data.extend(os.read(process.stderr.fileno(), 4096))
        assert ready.exists()
        assert b"answering" in stderr_data
        assert process.poll() is None
        release.write_text("release", encoding="utf-8")
        stdout, remaining_stderr = process.communicate(timeout=5)
        result = json.loads(stdout)
        all_stderr = bytes(stderr_data) + remaining_stderr
        assert process.returncode == 0
        assert result["output"] == "done"
        assert stdout.count(b"\n") == 1
        assert b"launching Codex" in all_stderr
        assert b"starting" in all_stderr
        assert b"finished with status success" in all_stderr
        progress_lines = [line for line in all_stderr.splitlines() if b"s answering" in line]
        assert len(progress_lines) == 1
    finally:
        selected.close()
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _progress_env(tmp_path: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }


def _fill_pipe(write_descriptor: int) -> None:
    flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
    fcntl.fcntl(write_descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    try:
        while True:
            os.write(write_descriptor, b"x" * 4096)
    except BlockingIOError:
        pass
    finally:
        fcntl.fcntl(write_descriptor, fcntl.F_SETFL, flags)


def _lock_is_available(lock_path: Path) -> bool:
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(stream, fcntl.LOCK_UN)
    return True


@pytest.mark.parametrize("collision", [False, True])
def test_progress_full_stderr_before_launch_does_not_block_or_change_flags(
    tmp_path: Path,
    collision: bool,
) -> None:
    marker = tmp_path / "full-before-launch"
    group_path = tmp_path / "full-before-launch.group"
    fixture = (
        f"import os;from pathlib import Path;Path({str(marker)!r}).touch();"
        f"Path({str(group_path)!r}).write_text(str(os.getpgrp()));" + CODEX_SUCCESS
    )
    config = write_agent(tmp_path, "codex", fixture, profile="work")
    if collision:
        global_path = tmp_path / "xdg/pratfall/config.toml"
        global_path.parent.mkdir(parents=True)
        global_path.write_text('version=1\n[profiles.work]\nagent="codex"\n')
    read_descriptor, write_descriptor = os.pipe()
    original_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
    _fill_pipe(write_descriptor)
    process: subprocess.Popen[bytes] | None = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pratfall",
                "cx",
                "prompt",
                "--progress",
                "--json",
                "--config",
                str(config),
            ],
            cwd=tmp_path,
            env=_progress_env(tmp_path),
            stdout=subprocess.PIPE,
            stderr=write_descriptor,
            start_new_session=True,
        )
        stdout = process.communicate(timeout=5)[0]
        result = json.loads(stdout)
        assert process.returncode == 0
        assert result["status"] == "success"
        assert json.loads(result["output"])["prompt"] == "prompt"
        assert stdout.count(b"\n") == 1
        assert marker.exists()
        assert time.monotonic() - started < 5
        restored_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
        assert restored_flags & os.O_NONBLOCK == original_flags & os.O_NONBLOCK
    finally:
        if process is not None and process.poll() is None and group_path.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
        if process is not None and process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        os.close(write_descriptor)
        os.close(read_descriptor)


def test_progress_full_stderr_during_execution_does_not_stall_deadline_or_cleanup(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "full-during-ready"
    release = tmp_path / "full-during-release"
    lock_path = tmp_path / "full-during.lock"
    group_path = tmp_path / "full-during.group"
    fixture = f"""import fcntl,json,os,time
lock = open({str(lock_path)!r}, "wb")
fcntl.flock(lock, fcntl.LOCK_EX)
open({str(group_path)!r}, "w").write(str(os.getpgrp()))
print(json.dumps({{"type":"item.completed","item":{{"id":"a","type":"agent_message","text":"partial"}}}}), flush=True)
open({str(ready)!r}, "w").write("ready")
while not os.path.exists({str(release)!r}): time.sleep(0.01)
time.sleep(30)
"""
    config = write_agent(tmp_path, "codex", fixture)
    read_descriptor, write_descriptor = os.pipe()
    original_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
    process: subprocess.Popen[bytes] | None = None
    cleaned = False
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pratfall",
                "cx",
                "prompt",
                "--progress",
                "--timeout",
                "1.2",
                "--json",
                "--config",
                str(config),
            ],
            cwd=tmp_path,
            env=_progress_env(tmp_path),
            stdout=subprocess.PIPE,
            stderr=write_descriptor,
            start_new_session=True,
        )
        deadline = time.monotonic() + 2
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        _fill_pipe(write_descriptor)
        release.touch()
        stdout = process.communicate(timeout=5)[0]
        result = json.loads(stdout)
        assert process.returncode == 124
        assert result["status"] == "timeout"
        assert result["output"] == "partial"
        assert stdout.count(b"\n") == 1
        assert time.monotonic() - started < 5
        cleaned = _lock_is_available(lock_path)
        assert cleaned
        restored_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
        assert restored_flags & os.O_NONBLOCK == original_flags & os.O_NONBLOCK
    finally:
        if not cleaned and group_path.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
        if process is not None and process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        os.close(write_descriptor)
        os.close(read_descriptor)


@pytest.mark.parametrize("native_diagnostic", [False, True])
@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("json_mode", [False, True])
def test_final_stderr_failure_preserves_output_and_cleans_owned_descendant(
    tmp_path: Path, native_diagnostic: bool, progress: bool, json_mode: bool
) -> None:
    ready = tmp_path / "final-failure-ready"
    release = tmp_path / "final-failure-release"
    lock_path = tmp_path / "final-failure.lock"
    pid_path = tmp_path / "final-failure.pid"
    group_path = tmp_path / "final-failure.group"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "open(sys.argv[3],'w').write(str(os.getpid()));"
        "os.close(1);os.close(2);os.write(int(sys.argv[1]),b'1');"
        "os.close(int(sys.argv[1]));time.sleep(30)"
    )
    diagnostic = "os.write(2,b'native diagnostic\\n')" if native_diagnostic else ""
    fixture = f"""import json,os,subprocess,sys,time
ready_read, ready_write = os.pipe()
subprocess.Popen([sys.executable, "-c", {descendant!r}, str(ready_write), {str(lock_path)!r}, {str(pid_path)!r}], pass_fds=(ready_write,))
os.close(ready_write)
os.read(ready_read, 1)
os.close(ready_read)
open({str(group_path)!r}, "w").write(str(os.getpgrp()))
open({str(ready)!r}, "w").write("ready")
while not os.path.exists({str(release)!r}): time.sleep(0.01)
print(json.dumps({{"type":"item.completed","item":{{"id":"a","type":"agent_message","text":"KEEP"}}}}), flush=True)
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}}}), flush=True)
{diagnostic}
"""
    config = write_agent(tmp_path, "codex", fixture)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pratfall",
            "cx",
            "prompt",
            *(["--progress"] if progress else []),
            *(["--json"] if json_mode else []),
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stderr_data = bytearray()
    cleaned = False
    launched = b"s starting" if progress else b"launching Codex"
    try:
        assert process.stderr is not None
        selected = selectors.DefaultSelector()
        selected.register(process.stderr, selectors.EVENT_READ)
        deadline = time.monotonic() + 5
        while (
            (not ready.exists() or launched not in stderr_data)
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            if selected.select(0.1):
                stderr_data.extend(os.read(process.stderr.fileno(), 4096))
        selected.close()
        assert ready.exists()
        assert launched in stderr_data
        process.stderr.close()
        process.stderr = None
        release.touch()
        stdout = process.communicate(timeout=6)[0]
        assert process.returncode == 1
        if json_mode:
            result = json.loads(stdout)
            assert result["status"] == "error"
            assert result["output"] == "KEEP"
            assert result["native_exit_code"] == 0
            assert result["error"]["code"] == "output_io_error"
            assert result["usage"]["input_tokens"] == 1
            assert result["usage"]["output_tokens"] == 1
        else:
            assert stdout == b"KEEP\n"
        assert stdout.count(b"\n") == 1
        cleaned = _lock_is_available(lock_path)
        assert cleaned
    finally:
        if not cleaned and group_path.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize("progress", [False, True])
def test_invalid_native_stderr_cleans_owned_descendant_after_parent_exit(
    tmp_path: Path, progress: bool
) -> None:
    ready = tmp_path / "encoding-failure-ready"
    lock_path = tmp_path / "encoding-failure.lock"
    group_path = tmp_path / "encoding-failure.group"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "os.close(1);os.close(2);os.write(int(sys.argv[1]),b'1');"
        "os.close(int(sys.argv[1]));time.sleep(30)"
    )
    fixture = f"""import json,os,subprocess,sys
ready_read, ready_write = os.pipe()
subprocess.Popen([sys.executable, "-c", {descendant!r}, str(ready_write), {str(lock_path)!r}], pass_fds=(ready_write,))
os.close(ready_write)
os.read(ready_read, 1)
os.close(ready_read)
open({str(group_path)!r}, "w").write(str(os.getpgrp()))
open({str(ready)!r}, "w").write("ready")
print(json.dumps({{"type":"item.completed","item":{{"id":"a","type":"agent_message","text":"KEEP"}}}}), flush=True)
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}}}), flush=True)
os.write(2, b"\\xff")
"""
    config = write_agent(tmp_path, "codex", fixture)
    arguments = [
        sys.executable,
        "-m",
        "pratfall",
        "cx",
        "prompt",
        "--json",
        "--config",
        str(config),
    ]
    if progress:
        arguments.append("--progress")
    cleaned = False
    process = subprocess.Popen(
        arguments,
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=6)
        result = json.loads(stdout)
        assert ready.read_text(encoding="utf-8") == "ready"
        assert process.returncode == 1
        assert result["status"] == "error"
        assert result["output"] == "KEEP"
        assert result["native_exit_code"] == 0
        assert result["error"] == {
            "code": "output_encoding",
            "message": "Agent stderr is not valid UTF-8.",
        }
        assert stdout.count(b"\n") == 1
        assert b"Traceback" not in stderr
        cleaned = _lock_is_available(lock_path)
        assert cleaned
    finally:
        if not cleaned and group_path.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _elapsed_progress(stderr: bytes | bytearray) -> list[tuple[float, str]]:
    pattern = re.compile(
        rb"prat: ([0-9]+\.[0-9])s (starting|working|reasoning|using tools|answering|finishing)"
    )
    return [
        (float(match.group(1)), match.group(2).decode())
        for line in stderr.splitlines()
        if (match := pattern.fullmatch(line)) is not None
    ]


def test_progress_coalesces_mixed_burst_and_emits_idle_heartbeat(tmp_path: Path) -> None:
    ready = tmp_path / "heartbeat-ready"
    release = tmp_path / "heartbeat-release"
    group_path = tmp_path / "heartbeat.group"
    payload_marker = "PROMPT-TOOL-ANSWER-CONTENT"
    fixture = f"""import json,os,time
events = [
    {{"type":"thread.started","thread_id":"fixture"}},
    {{"type":"turn.started"}},
    {{"type":"item.started","item":{{"id":"reason","type":"reasoning"}}}},
    {{"type":"item.updated","item":{{"id":"tool","type":"command_execution"}}}},
    {{"type":"item.completed","item":{{"id":"answer","type":"agent_message","text":"done"}}}},
    {{"type":"future.event","payload":{payload_marker!r}}},
]
for event in events: print(json.dumps(event), flush=True)
open({str(group_path)!r}, "w").write(str(os.getpgrp()))
open({str(ready)!r}, "w").write("ready")
while not os.path.exists({str(release)!r}): time.sleep(0.01)
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":0,"cached_input_tokens":0,"output_tokens":0}}}}), flush=True)
"""
    config = write_agent(tmp_path, "codex", fixture)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pratfall",
            "cx",
            "prompt",
            "--progress",
            "--timeout",
            "12",
            "--json",
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stderr_data = bytearray()
    started = time.monotonic()
    selected = selectors.DefaultSelector()
    try:
        assert process.stderr is not None
        selected.register(process.stderr, selectors.EVENT_READ)
        deadline = started + 8
        while len(_elapsed_progress(stderr_data)) < 3 and time.monotonic() < deadline:
            if selected.select(0.1):
                stderr_data.extend(os.read(process.stderr.fileno(), 4096))
        updates = _elapsed_progress(stderr_data)
        assert ready.exists()
        assert process.poll() is None
        assert [category for _elapsed, category in updates[:3]] == [
            "starting",
            "answering",
            "working",
        ]
        assert 4.5 <= updates[2][0] - updates[1][0] <= 6.5
        release.touch()
        stdout, remaining_stderr = process.communicate(timeout=5)
        all_stderr = bytes(stderr_data) + remaining_stderr
        result = json.loads(stdout)
        final_updates = _elapsed_progress(all_stderr)
        assert process.returncode == 0
        assert result["status"] == "success"
        assert result["output"] == "done"
        assert payload_marker.encode() not in all_stderr
        assert stdout.count(b"\n") == 1
        assert all(current[0] - previous[0] >= 0.9 for previous, current in pairwise(final_updates))
    finally:
        selected.close()
        if process.poll() is None:
            release.touch()
            if group_path.exists():
                with suppress(ProcessLookupError):
                    os.killpg(int(group_path.read_text()), signal.SIGKILL)
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize(("failure_at", "json_mode"), [("error", False), ("flush", True)])
def test_final_diagnostic_and_flush_failures_preserve_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_at: str,
    json_mode: bool,
) -> None:
    class FailingDiagnostics(io.StringIO):
        flushes = 0

        def write(self, value: str) -> int:
            if failure_at == "error" and value == "prat: native failed":
                raise OSError("diagnostic write failed")
            return super().write(value)

        def flush(self) -> None:
            self.flushes += 1
            if failure_at == "flush" and self.flushes == 2:
                raise OSError("diagnostic flush failed")
            super().flush()

    config = write_agent(tmp_path, "codex", "")
    process = ProcessResult(
        ConsumedCapture(
            DecodedOutput(
                output="KEEP",
                usage=Usage(input_tokens=3, output_tokens=2),
                reported_models=("native-model",),
                cost_usd=0.01,
                error=ResultError("provider_error", "native failed"),
            )
        ),
        b"",
        0,
        12,
        process_group=123,
    )
    cleaned: list[int] = []
    monkeypatch.setattr(dispatch_module, "run", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(dispatch_module, "cleanup_process_group", cleaned.append)
    monkeypatch.setattr(sys, "stderr", FailingDiagnostics())
    arguments = ["cx", "prompt", "--config", str(config)]
    if json_mode:
        arguments.append("--json")
    assert main(arguments) == 1
    output = capsys.readouterr().out
    if json_mode:
        result = json.loads(output)
        assert result["error"]["code"] == "output_io_error"
        assert result["output"] == "KEEP"
        assert result["native_exit_code"] == 0
        assert result["usage"]["input_tokens"] == 3
        assert result["usage"]["output_tokens"] == 2
        assert result["reported_models"] == ["native-model"]
        assert result["cost_usd"] == 0.01
    else:
        assert output == "KEEP\n"
    assert cleaned == [123]


def test_final_presentation_cleanup_failure_stays_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dispatch_module, "cleanup_process_group", lambda _group: "permission denied"
    )
    process = ProcessResult(RawCapture(), b"", 0, 12, process_group=123)
    updated = dispatch_module._after_run_presentation_failure(
        process, BrokenPipeError("closed diagnostics")
    )
    assert updated.error == ResultError(
        "output_io_error",
        "Cannot write diagnostics to stderr: closed diagnostics. "
        "Process-group cleanup failed: permission denied.",
    )
    assert updated.native_exit_code == 0


def test_unverified_bounded_cleanup_preserves_primary_failure_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []
    monkeypatch.setattr(runner_module, "TERMINATE_GRACE", 0)
    monkeypatch.setattr(runner_module, "FINAL_DRAIN_GRACE", 0)
    monkeypatch.setattr(runner_module, "_process_group_exists", lambda _group: True)
    monkeypatch.setattr(
        runner_module, "_signal_group", lambda _group, chosen: signals.append(chosen)
    )
    cleanup_error = runner_module.cleanup_process_group(123)
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert cleanup_error == "could not verify owned process-group cleanup before the deadline"

    monkeypatch.setattr(dispatch_module, "cleanup_process_group", lambda _group: cleanup_error)
    primary = ResultError("stdout_limit_exceeded", "primary output failure")
    process = ProcessResult(
        ConsumedCapture(DecodedOutput(output="KEEP", error=primary)),
        b"",
        17,
        12,
        error=primary,
        process_group=123,
    )
    updated = dispatch_module._after_run_presentation_failure(
        process, BrokenPipeError("closed diagnostics")
    )
    assert updated.error == ResultError(
        "stdout_limit_exceeded",
        "primary output failure Process-group cleanup failed: could not verify "
        "owned process-group cleanup before the deadline.",
    )
    assert updated.native_exit_code == 17
    assert updated.capture == ConsumedCapture(DecodedOutput(output="KEEP", error=primary))


def test_quiet_run_has_no_elapsed_progress_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    assert main(["cx", "prompt", "--config", str(config), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "success"
    assert "s starting" not in captured.err
    assert "s working" not in captured.err


@pytest.mark.parametrize("progress", [False, True])
def test_closed_stderr_preserves_normalized_json_and_does_not_launch(
    tmp_path: Path, progress: bool
) -> None:
    marker = tmp_path / "launched"
    config = write_agent(
        tmp_path,
        "codex",
        f"from pathlib import Path;Path({str(marker)!r}).touch()",
    )
    bootstrap = """
import os,sys
os.close(2)
from pratfall.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "cx",
            "prompt",
            *(["--progress"] if progress else []),
            "--json",
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=5,
        check=False,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["error"]["code"] == "output_io_error"
    assert result["output"] == ""
    assert not marker.exists()


def test_progress_restores_stderr_flags_and_replays_native_diagnostics_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    diagnostics = tmp_path / "diagnostics.log"
    with diagnostics.open("w", encoding="utf-8") as stream:
        original_flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
        monkeypatch.setattr(sys, "stderr", stream)
        assert main(["cx", "prompt", "--progress", "--json", "--config", str(config)]) == 0
        restored_flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
    result = json.loads(capsys.readouterr().out)
    written = diagnostics.read_text(encoding="utf-8")
    assert result["status"] == "success"
    assert restored_flags & os.O_NONBLOCK == original_flags & os.O_NONBLOCK
    assert written.count("native diagnostic") == 1
    assert "s starting" in written
    assert "finished with status success" in written


def test_native_stderr_write_failure_preserves_results_and_cleans_up_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingNativeReplay(io.StringIO):
        def write(self, value: str) -> int:
            if value == "native noise\n":
                raise OSError("native replay failed")
            return super().write(value)

    config = write_agent(tmp_path, "codex", "")
    process = ProcessResult(
        ConsumedCapture(
            DecodedOutput(
                output="KEEP",
                usage=Usage(input_tokens=3, output_tokens=2),
                reported_models=("native-model",),
                cost_usd=0.01,
            )
        ),
        b"native noise\n",
        0,
        12,
        process_group=123,
    )
    cleaned: list[int] = []
    stream = FailingNativeReplay()
    monkeypatch.setattr(dispatch_module, "run", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(dispatch_module, "cleanup_process_group", cleaned.append)
    monkeypatch.setattr(sys, "stderr", stream)
    assert main(["cx", "prompt", "--config", str(config), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == {
        "code": "output_io_error",
        "message": "Cannot write diagnostics to stderr: native replay failed.",
    }
    assert result["output"] == "KEEP"
    assert result["usage"]["input_tokens"] == 3
    assert result["reported_models"] == ["native-model"]
    assert result["cost_usd"] == 0.01
    assert cleaned == [123]
    assert stream.getvalue() == "prat: launching Codex\n"


def test_progress_stderr_failure_restores_flags_without_redirecting_the_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = tmp_path / "launched"
    config = write_agent(
        tmp_path,
        "codex",
        f"from pathlib import Path;Path({str(marker)!r}).touch()",
    )
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    stream = os.fdopen(write_fd, "w", encoding="utf-8")
    original = fcntl.fcntl(write_fd, fcntl.F_GETFL)
    monkeypatch.setattr(sys, "stderr", stream)
    exit_code = main(["cx", "prompt", "--progress", "--json", "--config", str(config)])
    restored = fcntl.fcntl(write_fd, fcntl.F_GETFL)
    mode = os.fstat(write_fd).st_mode
    with suppress(OSError):
        stream.close()
    assert exit_code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "output_io_error"
    assert not marker.exists()
    assert original & os.O_NONBLOCK == 0
    assert restored & os.O_NONBLOCK == 0
    assert stat.S_ISFIFO(mode)


def test_progress_run_toggles_stderr_nonblocking_once_including_config_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_config = write_agent(tmp_path, "codex", CODEX_SUCCESS, profile="work")
    global_path = init_config()
    global_path.write_text(fake_config.read_text(), encoding="utf-8")
    local_path = tmp_path / ".pratfile"
    local_path.write_text('version=1\n[profiles.work]\nagent="cx"\n', encoding="utf-8")
    diagnostics = tmp_path / "diagnostics.log"
    with diagnostics.open("w", encoding="utf-8") as stream:
        descriptor = stream.fileno()
        original = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        real_fcntl = fcntl.fcntl
        applied: list[int] = []

        def recording(fd: int, operation: int, *rest: int) -> int:
            if operation == fcntl.F_SETFL and fd == descriptor:
                applied.append(rest[0])
            return real_fcntl(fd, operation, *rest)

        monkeypatch.setattr(fcntl, "fcntl", recording)
        monkeypatch.setattr(sys, "stderr", stream)
        exit_code = main(["work", "prompt", "--progress", "--json"])
        restored = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
    written = diagnostics.read_text(encoding="utf-8")
    assert f"prat: warning: {local_path}: profile 'work' replaces" in written
    assert "finished with status success" in written
    assert applied == [original | os.O_NONBLOCK, original]
    assert restored & os.O_NONBLOCK == original & os.O_NONBLOCK


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
        (["--max-turns=4294967296", "cc", "prompt"], "positive integer"),
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
        "reported_models",
        "cost_usd",
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


def test_broken_output_pipe_returns_one_after_child_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "finished"
    code = f"from pathlib import Path;Path({str(marker)!r}).write_text('done');" + CODEX_SUCCESS
    config = write_agent(tmp_path, "codex", code)
    monkeypatch.setattr(sys, "stdout", BrokenOutput())
    assert main(["cx", "prompt", "--config", str(config)]) == 1
    assert marker.read_text(encoding="utf-8") == "done"


def _run_with_closed_stdout(
    tmp_path: Path, arguments: list[str]
) -> subprocess.CompletedProcess[bytes]:
    consumer = tmp_path / "consumer"
    consumer.mkdir(exist_ok=True)
    read_descriptor, write_descriptor = os.pipe()
    os.close(read_descriptor)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pratfall", *arguments],
            cwd=consumer,
            env=env,
            stdout=write_descriptor,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_descriptor)
    stderr = process.communicate(timeout=10)[1]
    return subprocess.CompletedProcess(process.args, process.returncode, b"", stderr)


def test_management_output_to_early_closed_pipe_returns_one(tmp_path: Path) -> None:
    completed = _run_with_closed_stdout(tmp_path, ["agents"])
    assert completed.returncode == 1
    assert b"BrokenPipeError" not in completed.stderr


@pytest.mark.parametrize("argument", ["--help", "--version"])
def test_argparse_output_to_early_closed_pipe_returns_one(tmp_path: Path, argument: str) -> None:
    completed = _run_with_closed_stdout(tmp_path, [argument])
    assert completed.returncode == 1
    assert b"BrokenPipeError" not in completed.stderr


@pytest.mark.parametrize("json_mode", [False, True])
def test_successful_fake_agent_output_to_early_closed_pipe_returns_one(
    tmp_path: Path, json_mode: bool
) -> None:
    fake = tmp_path / "fake.py"
    fake.write_text(CLAUDE_SUCCESS, encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f"version=1\n[agents.claude]\ncommand={json.dumps([sys.executable, str(fake)])}\n",
        encoding="utf-8",
    )
    arguments = ["cc", "prompt", "--config", str(config)]
    if json_mode:
        arguments.append("--json")
    completed = _run_with_closed_stdout(tmp_path, arguments)
    assert completed.returncode == 1
    assert b"BrokenPipeError" not in completed.stderr
    assert b"finished with status success" in completed.stderr


GEMINI_SURROGATE_ERROR = r"""import sys
sys.stdout.write('{"error":{"type":"x","message":"bad\\ud800"}}')
"""


def test_surrogate_provider_message_emits_one_normalized_json_result(tmp_path: Path) -> None:
    config = write_agent(tmp_path, "gemini", GEMINI_SURROGATE_ERROR)
    completed = subprocess.run(
        [sys.executable, "-m", "pratfall", "gm", "prompt", "--json", "--config", str(config)],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        capture_output=True,
        timeout=20,
        check=False,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert completed.stdout.count(b"\n") == 1
    assert result["status"] == "error"
    assert result["agent"] == "gemini"
    assert result["error"]["code"] == "internal_error"
    assert "UnicodeEncodeError" in result["error"]["message"]
    assert b"Traceback" not in completed.stderr


def test_surrogate_provider_message_in_text_mode_keeps_the_native_diagnostic(
    tmp_path: Path,
) -> None:
    config = write_agent(tmp_path, "gemini", GEMINI_SURROGATE_ERROR)
    completed = subprocess.run(
        [sys.executable, "-m", "pratfall", "gm", "prompt", "--config", str(config)],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == b""
    assert b"Traceback" not in completed.stderr
    assert b"prat: bad\\ud800\n" in completed.stderr


def _colliding_profiles(tmp_path: Path, count: int) -> Path:
    names = [f"p{index:04d}" for index in range(count)]
    bodies = "".join(f'[profiles.{name}]\nagent="gemini"\n' for name in names)
    command = json.dumps([sys.executable, "-c", "pass"])
    global_path = tmp_path / "xdg" / "pratfall" / "config.toml"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        f"version=1\n[agents.gemini]\ncommand={command}\n{bodies}", encoding="utf-8"
    )
    local_path = tmp_path / "local.toml"
    local_path.write_text(f"version=1\n{bodies}", encoding="utf-8")
    return local_path


def _drain(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        data = os.read(descriptor, 1 << 20)
        if not data:
            return b"".join(chunks)
        chunks.append(data)


def test_progress_dry_run_preview_into_full_shared_stream_normalizes_without_traceback(
    tmp_path: Path,
) -> None:
    local_path = _colliding_profiles(tmp_path, 1000)
    read_descriptor, write_descriptor = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pratfall",
            "p0000",
            "-",
            "--progress",
            "--dry-run",
            "--json",
            "--config",
            str(local_path),
        ],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        stdin=subprocess.PIPE,
        stdout=write_descriptor,
        stderr=write_descriptor,
        start_new_session=True,
    )
    os.close(write_descriptor)
    try:
        assert process.stdin is not None
        process.stdin.write(b"Z" * 40000)
        process.stdin.close()
        time.sleep(1.0)
        assert process.poll() is None
        output = _drain(read_descriptor)
        assert process.wait(timeout=10) == 1
        assert b"Traceback" not in output
        assert b"prat: Unexpected failure: BlockingIOError" in output
        # Whether the preview survives depends on where it lands against the pipe
        # buffer; test_dry_run_is_a_single_versioned_preview_and_does_not_launch
        # pins that it is emitted exactly once.
        assert output.count(b'"status": "error"') == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(read_descriptor)


def _decoder_failure_shim(config: Path) -> str:
    return (
        "import sys\n"
        "from pratfall.adapters.registry import ADAPTERS\n"
        "from pratfall.cli import main\n"
        "def boom(_stdout):\n"
        "    raise KeyError('decoder blew up')\n"
        'object.__setattr__(ADAPTERS["gemini"], "decode", boom)\n'
        f"sys.exit(main(['gm', 'prompt', '--json', '--config', {str(config)!r}]))\n"
    )


def test_decoder_exception_normalizes_result_and_cleans_owned_descendant(tmp_path: Path) -> None:
    lock_path = tmp_path / "decoder-failure.lock"
    group_path = tmp_path / "decoder-failure.group"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "os.close(1);os.close(2);os.write(int(sys.argv[1]),b'1');"
        "os.close(int(sys.argv[1]));time.sleep(30)"
    )
    fixture = f"""import json,os,subprocess,sys
ready_read, ready_write = os.pipe()
subprocess.Popen([sys.executable, "-c", {descendant!r}, str(ready_write), {str(lock_path)!r}], pass_fds=(ready_write,))
os.close(ready_write)
os.read(ready_read, 1)
os.close(ready_read)
open({str(group_path)!r}, "w").write(str(os.getpgrp()))
print(json.dumps({{"response": "KEEP"}}), flush=True)
"""
    config = write_agent(tmp_path, "gemini", fixture)
    cleaned = False
    process = subprocess.Popen(
        [sys.executable, "-c", _decoder_failure_shim(config)],
        cwd=tmp_path,
        env=_progress_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=20)
        result = json.loads(stdout)
        assert process.returncode == 1
        assert stdout.count(b"\n") == 1
        assert result["status"] == "error"
        assert result["native_exit_code"] == 0
        assert result["error"] == {
            "code": "internal_error",
            "message": "Unexpected failure: KeyError: 'decoder blew up'",
        }
        assert b"Traceback" not in stderr
        cleaned = _lock_is_available(lock_path)
        assert cleaned
    finally:
        if not cleaned and group_path.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


class PartialStdout(io.StringIO):
    def write(self, value: str) -> int:
        if value == "\n":
            raise ValueError("stdout is broken")
        return super().write(value)


def test_failure_after_emitted_output_adds_no_second_stdout_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_agent(tmp_path, "codex", CODEX_SUCCESS)
    stdout = PartialStdout()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    assert main(["cx", "prompt", "--json", "--config", str(config)]) == 1
    emitted = stdout.getvalue()
    assert emitted.count('"schema_version"') == 1
    assert json.loads(emitted)["status"] == "success"
    assert "prat: Unexpected failure: ValueError: stdout is broken" in stderr.getvalue()


def test_unrepresentable_result_text_emits_a_normalized_object_with_resolved_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_agent(tmp_path, "gemini", GEMINI_SURROGATE_ERROR, profile="work")
    assert main(["work", "prompt", "--json", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.out.count("\n") == 1
    assert result["agent"] == "gemini"
    assert result["profile"] == "work"
    assert result["status"] == "error"
    assert result["error"]["code"] == "internal_error"
    assert "UnicodeEncodeError" in result["error"]["message"]


def test_decoder_exception_is_normalized_with_process_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(_resolved: object, _stdout: str) -> DecodedOutput:
        raise KeyError("decoder blew up")

    config = write_agent(tmp_path, "gemini", 'print("{\\"response\\": \\"KEEP\\"}")')
    cleaned: list[int] = []
    monkeypatch.setattr(dispatch_module, "_decode", boom)
    monkeypatch.setattr(dispatch_module, "cleanup_process_group", cleaned.append)
    assert main(["gm", "prompt", "--json", "--config", str(config)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert cleaned
    assert result["status"] == "error"
    assert result["native_exit_code"] == 0
    assert result["error"] == {
        "code": "internal_error",
        "message": "Unexpected failure: KeyError: 'decoder blew up'",
    }


GEMINI_NON_ASCII = """import json, sys
sys.stdout.buffer.write(json.dumps({"response": "caf\\u00e9 \\u4e16\\u754c"}).encode() + b"\\n")
"""


def _ascii_stdout_env(tmp_path: Path) -> dict[str, str]:
    return _progress_env(tmp_path) | {"PYTHONIOENCODING": "ascii"}


def test_result_unencodable_for_stdout_emits_exactly_one_json_object(tmp_path: Path) -> None:
    config = write_agent(tmp_path, "gemini", GEMINI_NON_ASCII, profile="work")
    completed = subprocess.run(
        [sys.executable, "-m", "pratfall", "work", "prompt", "--json", "--config", str(config)],
        cwd=tmp_path,
        env=_ascii_stdout_env(tmp_path),
        capture_output=True,
        timeout=20,
        check=False,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert completed.stdout.count(b"\n") == 1
    assert result["agent"] == "gemini"
    assert result["profile"] == "work"
    assert result["status"] == "error"
    assert result["error"]["code"] == "internal_error"
    assert "UnicodeEncodeError" in result["error"]["message"]
    assert b"Traceback" not in completed.stderr


def test_result_unencodable_for_stdout_in_text_mode_reports_on_stderr(tmp_path: Path) -> None:
    config = write_agent(tmp_path, "gemini", GEMINI_NON_ASCII)
    completed = subprocess.run(
        [sys.executable, "-m", "pratfall", "gm", "prompt", "--config", str(config)],
        cwd=tmp_path,
        env=_ascii_stdout_env(tmp_path),
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == b""
    assert b"prat: Unexpected failure: UnicodeEncodeError" in completed.stderr
    assert b"Traceback" not in completed.stderr
