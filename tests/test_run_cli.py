import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pratfall.cli import main
from pratfall.prompt_input import PROMPT_LIMIT


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
                "ki",
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
    assert result["error"]["code"] == "invalid_config"
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
    assert result["error"]["code"] == "invalid_config"
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
