#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

HARNESS_TIMEOUT = 15.0
CLEANUP_TIMEOUT = 2.0

AGENTS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "antigravity": "agy",
    "copilot": "copilot",
    "kiro": "kiro-cli",
    "cursor": "agent",
    "openclaw": "openclaw",
    "hermes": "hermes",
    "opencode": "opencode",
}
ALIASES = {
    "cc": "claude",
    "cx": "codex",
    "gm": "gemini",
    "ag": "antigravity",
    "cp": "copilot",
    "ki": "kiro",
    "cu": "cursor",
    "claw": "openclaw",
    "hm": "hermes",
    "oc": "opencode",
}


def _prompt(agent: str, arguments: list[str], data: bytes) -> str:
    if agent == "antigravity":
        return json.loads(data)["message"]["content"]
    if agent in {"gemini", "copilot"}:
        return next(item.split("=", 1)[1] for item in arguments if item.startswith("--prompt="))
    if agent in {"cursor", "kiro"}:
        return arguments[arguments.index("--") + 1]
    return data.decode()


def _answer(agent: str, answer: str) -> None:
    if agent == "claude":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": answer,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )
        )
    elif agent == "codex":
        print(json.dumps({"type": "turn.started"}))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "answer", "type": "agent_message", "text": answer},
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 3,
                        "cached_input_tokens": 1,
                        "output_tokens": 2,
                    },
                }
            )
        )
    elif agent == "gemini":
        print(json.dumps({"response": answer}))
    elif agent == "antigravity":
        print(json.dumps({"event": "init", "init": {"cwd": os.getcwd()}}))
        print(
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": answer,
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "thinking_tokens": 1,
                            "cache_read_tokens": 1,
                            "total_tokens": 5,
                        },
                    },
                }
            )
        )
    elif agent == "copilot":
        print(
            json.dumps(
                {
                    "type": "assistant.message",
                    "data": {"messageId": "answer", "content": answer},
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "timestamp": "2026-09-09T12:00:00Z",
                    "sessionId": "qa",
                    "exitCode": 0,
                    "usage": {
                        "premiumRequests": 1,
                        "totalApiDurationMs": 2,
                        "sessionDurationMs": 3,
                        "codeChanges": {
                            "linesAdded": 0,
                            "linesRemoved": 0,
                            "filesModified": 0,
                        },
                    },
                }
            )
        )
    elif agent == "cursor":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": answer,
                }
            )
        )
    elif agent == "openclaw":
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": "ok",
                    "final": answer,
                    "payloads": [{"text": answer}],
                    "usage": {"input": 3, "output": 2, "total": 5},
                }
            )
        )
    elif agent == "opencode":
        print(
            json.dumps(
                {
                    "type": "text",
                    "part": {
                        "id": "answer",
                        "type": "text",
                        "text": answer,
                        "time": {"end": 1},
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "id": "step",
                        "type": "step-finish",
                        "reason": "stop",
                        "cost": 0,
                        "tokens": {
                            "total": 5,
                            "input": 3,
                            "output": 2,
                            "reasoning": 0,
                            "cache": {"read": 1, "write": 0},
                        },
                    },
                }
            )
        )
    else:
        print(answer)


def _fake_native(agent: str, arguments: list[str]) -> int:
    _record_owner()
    data = sys.stdin.buffer.read()
    prompt = _prompt(agent, arguments, data)
    record = {
        "agent": agent,
        "argv": arguments,
        "stdin": data.decode(),
        "prompt": prompt,
        "cwd": os.getcwd(),
    }
    log_path = Path(os.environ["PRAT_QA_LOG"])
    descriptor = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(descriptor)
    if prompt.startswith("QA_PROVIDER_FAILURE"):
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "errors": ["controlled provider failure"],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                }
            )
        )
        return 0
    if prompt.startswith("QA_TRUNCATED"):
        print('{"type":"turn.started"}')
        return 0
    if prompt.startswith("QA_NATIVE_17"):
        _answer(agent, "native failure answer")
        return 17
    if prompt.startswith(
        (
            "QA_TIMEOUT",
            "QA_INTERRUPT",
            "QA_PIPE_HOLDER",
            "QA_STDOUT_LIMIT",
            "QA_STDERR_LIMIT",
        )
    ):
        lock_path, ready_path, go_path = (
            Path(item.split("=", 1)[1]) for item in prompt.split()[1:4]
        )
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--lock-holder",
                str(lock_path),
                str(ready_path),
            ]
        )
        while not ready_path.exists():
            time.sleep(0.01)
        if prompt.startswith("QA_PIPE_HOLDER"):
            return 0
        if prompt.startswith(("QA_STDOUT_LIMIT", "QA_STDERR_LIMIT")):
            while not go_path.exists():
                time.sleep(0.01)
        if prompt.startswith("QA_STDOUT_LIMIT"):
            os.write(1, b"x" * (8 * 1024 * 1024 + 1))
        if prompt.startswith("QA_STDERR_LIMIT"):
            os.write(2, b"x" * (2 * 1024 * 1024 + 1))
        time.sleep(30)
    if prompt.startswith("QA_BAD_UTF8"):
        os.write(1, b"\xff")
        return 0
    answer = json.dumps(record, ensure_ascii=False, sort_keys=True)
    _answer(agent, answer)
    print(f"fake diagnostic: {agent}", file=sys.stderr)
    return 0


def _lock_holder(lock_path: Path, ready_path: Path) -> int:
    _record_owner()
    with lock_path.open("wb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        ready_path.write_text("ready\n", encoding="utf-8")
        time.sleep(30)
    return 0


def _record_owner() -> None:
    owner_path = os.environ.get("PRAT_QA_OWNER")
    if owner_path is None:
        return
    record = json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}) + "\n"
    descriptor = os.open(owner_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, record.encode())
    finally:
        os.close(descriptor)


def _write_fakes(root: Path, python: Path, script: Path) -> Path:
    directory = root / "bin"
    directory.mkdir(exist_ok=True)
    for executable in AGENTS.values():
        agent = next(name for name, command in AGENTS.items() if command == executable)
        wrapper = directory / executable
        wrapper.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(str(python))
            + " "
            + shlex.quote(str(script))
            + " --fake-native "
            + shlex.quote(agent)
            + ' "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return directory


def _write_config(path: Path, python: Path, script: Path) -> None:
    lines = ["version = 1", "", "[defaults]", "timeout = 7", ""]
    for agent in AGENTS:
        command = json.dumps([str(python), str(script), "--fake-native", agent])
        lines.extend((f"[agents.{agent}]", f"command = {command}", ""))
    lines.extend(
        (
            "[profiles.simple]",
            'agent = "codex"',
            'model = "profile-model"',
            'effort = "medium"',
            'native_args = ["--sandbox", "read-only"]',
            "",
            "[profiles.override]",
            'agent = "claude"',
            'model = "profile-model"',
            'effort = "medium"',
            "timeout = 9",
            "max_budget_usd = 2",
            "max_turns = 3",
            'native_args = ["--allowed-tools", "Read"]',
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class _Invocation:
    process: subprocess.Popen[bytes]
    owner_path: Path
    stdin: bytes | None


@dataclass(frozen=True)
class _AsyncFixture:
    invocation: _Invocation
    lock_path: Path
    ready_path: Path
    go_path: Path


def _start(
    command: list[str], root: Path, env: dict[str, str], *, stdin: bytes | None = None
) -> _Invocation:
    owners = root / "owners"
    owners.mkdir(exist_ok=True)
    owner_path = owners / f"invocation-{time.monotonic_ns()}.jsonl"
    env["PRAT_QA_OWNER"] = str(owner_path)
    process = subprocess.Popen(
        command,
        cwd=root / "consumer",
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    return _Invocation(process, owner_path, stdin)


def _owned_process_groups(owner_path: Path) -> set[int]:
    if not owner_path.exists():
        return set()
    groups = set()
    for line in owner_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        pid, group = int(record["pid"]), int(record["pgid"])
        with suppress(ProcessLookupError):
            if os.getpgid(pid) == group:
                groups.add(group)
    return groups


def _signal_group(group: int, signum: signal.Signals) -> None:
    if group == os.getpgrp():
        raise RuntimeError("refusing to signal the QA harness process group")
    with suppress(ProcessLookupError):
        os.killpg(group, signum)


def _signal_owned(invocation: _Invocation, signum: signal.Signals) -> None:
    for group in _owned_process_groups(invocation.owner_path):
        _signal_group(group, signum)
    _signal_group(invocation.process.pid, signum)


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _cleanup(invocation: _Invocation) -> None:
    _signal_owned(invocation, signal.SIGKILL)
    if invocation.process.poll() is None:
        try:
            invocation.process.wait(timeout=CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            _close_pipes(invocation.process)
            raise RuntimeError("QA invocation could not be reaped after cleanup") from error
    invocation.owner_path.unlink(missing_ok=True)


def _communicate(
    invocation: _Invocation, *, timeout: float = HARNESS_TIMEOUT
) -> tuple[bytes, bytes]:
    process = invocation.process
    try:
        return process.communicate(input=invocation.stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_owned(invocation, signal.SIGINT)
        try:
            process.communicate(timeout=CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            _signal_owned(invocation, signal.SIGKILL)
            try:
                process.communicate(timeout=CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired as error:
                _close_pipes(process)
                raise RuntimeError("QA invocation pipes could not be drained") from error
        raise


def _run(
    prat: Path,
    root: Path,
    arguments: list[str],
    *,
    stdin: bytes | None = None,
    config: Path | None = None,
    path: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = [str(prat)]
    if config is not None:
        command.extend(("--config", str(config)))
    command.extend(arguments)
    env = os.environ.copy()
    env["PRAT_QA_LOG"] = str(root / "native.jsonl")
    env["XDG_CONFIG_HOME"] = str(root / "xdg")
    env["PATH"] = f"{path or root / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    invocation = _start(command, root, env, stdin=stdin)
    try:
        stdout, stderr = _communicate(invocation)
        return subprocess.CompletedProcess(command, invocation.process.returncode, stdout, stderr)
    finally:
        _cleanup(invocation)


def _json_result(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    return json.loads(completed.stdout)


def _assert_result(
    completed: subprocess.CompletedProcess[bytes],
    *,
    returncode: int,
    status: str,
    native_exit_code: int | None,
    error_code: str | None,
    error_message: str | None = None,
) -> dict[str, object]:
    assert len(completed.stdout.splitlines()) == 1
    result = _json_result(completed)
    assert completed.returncode == returncode
    assert result["schema_version"] == 1
    assert "reported_models" in result
    assert "cost_usd" in result
    assert result["status"] == status
    assert result["exit_code"] == returncode
    assert result["native_exit_code"] == native_exit_code
    if error_code is None:
        assert result["error"] is None
    else:
        assert result["error"] == {"code": error_code, "message": error_message}
    return result


def _calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _available(lock_path: Path) -> bool:
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(stream, fcntl.LOCK_UN)
    return True


def _pending(fixture: _AsyncFixture) -> bool:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not fixture.ready_path.exists():
        time.sleep(0.01)
    try:
        return (
            fixture.invocation.process.poll() is None
            and fixture.ready_path.exists()
            and not _available(fixture.lock_path)
        )
    finally:
        fixture.go_path.write_text("go\n", encoding="utf-8")


def _async_run(prat: Path, root: Path, config: Path, prompt: str, timeout: str) -> _AsyncFixture:
    name = prompt.lower()
    lock_path = root / f"{name}.lock"
    ready_path = root / f"{name}.ready"
    go_path = root / f"{name}.go"
    lock_path.unlink(missing_ok=True)
    ready_path.unlink(missing_ok=True)
    go_path.unlink(missing_ok=True)
    full_prompt = f"{prompt} LOCK={lock_path} READY={ready_path} GO={go_path}"
    env = os.environ.copy()
    env["PRAT_QA_LOG"] = str(root / "native.jsonl")
    env["XDG_CONFIG_HOME"] = str(root / "xdg")
    command = [
        str(prat),
        "--config",
        str(config),
        "cx",
        full_prompt,
        "--timeout",
        timeout,
        "--json",
    ]
    invocation = _start(command, root, env)
    return _AsyncFixture(invocation, lock_path, ready_path, go_path)


def _complete_async(
    fixture: _AsyncFixture, *, interrupt: bool = False
) -> tuple[subprocess.CompletedProcess[bytes], bool, bool]:
    process = fixture.invocation.process
    try:
        pending = _pending(fixture)
        if interrupt:
            _signal_group(process.pid, signal.SIGINT)
        stdout, stderr = _communicate(fixture.invocation)
        released = _available(fixture.lock_path)
        completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        return completed, pending, released
    finally:
        if fixture.invocation.owner_path.exists() or process.poll() is None:
            _cleanup(fixture.invocation)


def _compact_result(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    result = _json_result(completed)
    return {
        "returncode": completed.returncode,
        "status": result["status"],
        "native_exit_code": result["native_exit_code"],
        "error": result["error"],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _runtime_manifest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _archive_runtime_manifest(path: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                parts = Path(name).parts
                if len(parts) >= 2 and parts[0] == "pratfall" and name.endswith(".py"):
                    manifest[str(Path(*parts[1:]))] = _content_sha256(archive.read(name))
        return manifest
    with tarfile.open(path, "r:gz") as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            parts = Path(member.name).parts
            with suppress(ValueError):
                package_index = parts.index("pratfall", parts.index("src") + 1)
                if member.isfile() and member.name.endswith(".py"):
                    stream = archive.extractfile(member)
                    assert stream is not None
                    manifest[str(Path(*parts[package_index + 1 :]))] = _content_sha256(
                        stream.read()
                    )
    return manifest


def _module_root(python: Path) -> Path:
    command = "import pathlib,pratfall;print(pathlib.Path(pratfall.__file__).resolve().parent)"
    return Path(
        subprocess.check_output(
            [str(python), "-c", command], text=True, timeout=HARNESS_TIMEOUT
        ).strip()
    )


def _assert_install_identity(
    source_root: Path,
    installed_root: Path,
    environment_root: Path,
    source_manifest: dict[str, str],
    installed_manifest: dict[str, str],
    archive_manifest: dict[str, str],
) -> None:
    assert not installed_root.is_relative_to(source_root)
    assert installed_root.is_relative_to(environment_root)
    assert installed_manifest == source_manifest
    assert archive_manifest == installed_manifest


def _runtime_identity(
    repository: Path, prat: Path, sdist_prat: Path, wheel: Path, sdist: Path
) -> dict[str, object]:
    source_root = (repository / "src" / "pratfall").resolve()
    wheel_root = _module_root(prat.with_name("python")).resolve()
    sdist_root = _module_root(sdist_prat.with_name("python")).resolve()
    source_manifest = _runtime_manifest(source_root)
    wheel_installed_manifest = _runtime_manifest(wheel_root)
    sdist_installed_manifest = _runtime_manifest(sdist_root)
    wheel_archive_manifest = _archive_runtime_manifest(wheel)
    sdist_archive_manifest = _archive_runtime_manifest(sdist)
    assert source_manifest
    _assert_install_identity(
        source_root,
        wheel_root,
        prat.parent.parent.resolve(),
        source_manifest,
        wheel_installed_manifest,
        wheel_archive_manifest,
    )
    _assert_install_identity(
        source_root,
        sdist_root,
        sdist_prat.parent.parent.resolve(),
        source_manifest,
        sdist_installed_manifest,
        sdist_archive_manifest,
    )
    return {
        "source_module_root": str(source_root),
        "wheel_module_root": str(wheel_root),
        "sdist_module_root": str(sdist_root),
        "source_runtime_files": source_manifest,
        "wheel_installed_runtime_files": wheel_installed_manifest,
        "sdist_installed_runtime_files": sdist_installed_manifest,
        "wheel_archive_runtime_files": wheel_archive_manifest,
        "sdist_archive_runtime_files": sdist_archive_manifest,
        "wheel_module_outside_source": True,
        "sdist_module_outside_source": True,
        "wheel_runtime_matches_source": True,
        "sdist_runtime_matches_source": True,
        "wheel_archive_matches_installed": True,
        "sdist_archive_matches_installed": True,
    }


def _portable(value: object, repository: Path) -> object:
    if isinstance(value, str):
        return value.replace(str(repository), "$REPO")
    if isinstance(value, list):
        return [_portable(item, repository) for item in value]
    if isinstance(value, dict):
        return {key: _portable(item, repository) for key, item in value.items()}
    return value


def _entry_point_versions(
    prat: Path, sdist_prat: Path, root: Path, expected_version: str
) -> tuple[subprocess.CompletedProcess[bytes], subprocess.CompletedProcess[bytes]]:
    expected = f"prat {expected_version}"
    wheel_version = _run(prat, root, ["--version"])
    sdist_version = _run(sdist_prat, root, ["--version"])
    assert wheel_version.returncode == sdist_version.returncode == 0
    assert wheel_version.stdout.decode().strip() == expected
    assert sdist_version.stdout.decode().strip() == expected
    return wheel_version, sdist_version


def _exercise(  # noqa: PLR0913, PLR0915, PLR0917
    prat: Path,
    sdist_prat: Path,
    wheel: Path,
    sdist: Path,
    base_revision: str,
    root: Path,
    output: Path,
    expected_version: str,
) -> int:
    root.mkdir(parents=True, exist_ok=True)
    consumer = root / "consumer"
    consumer.mkdir(exist_ok=True)
    (root / "xdg").mkdir(exist_ok=True)
    log = root / "native.jsonl"
    log.unlink(missing_ok=True)
    script = Path(__file__).resolve()
    python = prat.with_name("python")
    fake_bin = _write_fakes(root, python, script)
    config = root / "config.toml"
    _write_config(config, python, script)
    results: dict[str, object] = {}

    version, _sdist_version = _entry_point_versions(prat, sdist_prat, root, expected_version)
    help_result = _run(prat, root, ["--help"])
    assert version.returncode == help_result.returncode == 0
    version_text = version.stdout.decode().strip()
    help_text = help_result.stdout.decode()
    expected_version_text = f"prat {expected_version}"
    assert version_text == expected_version_text
    assert help_text.startswith("usage: prat ")
    for expected in (
        "Run installed coding agents through named profiles.",
        "{agents,profiles,doctor,config}",
        "prat [RUN_OPTIONS] SELECTOR PROMPT [-- NATIVE_ARGS]",
        "--dry-run               Resolve and print the invocation without launching it.",
    ):
        assert expected in help_text
    results["Q01"] = {
        "status": "Pass",
        "version": version_text,
        "help": help_text,
    }

    results["Q02"] = {"status": "Pass", "version": expected_version_text}

    prompt = "fix this bug"
    completed = _run(prat, root, ["cc", prompt, "--json"], config=config)
    result = _json_result(completed)
    call = _calls(log)[-1]
    assert result["status"] == "success" and result["agent"] == "claude"
    assert result["exit_code"] == result["native_exit_code"] == completed.returncode == 0
    assert result["error"] is None
    assert json.loads(result["output"])["prompt"] == prompt
    assert call == {
        "agent": "claude",
        "argv": ["-p", "--output-format", "json"],
        "stdin": prompt,
        "prompt": prompt,
        "cwd": str(consumer),
    }
    results["Q03"] = {"status": "Pass", "result": _compact_result(completed), "native": call}

    completed = _run(prat, root, ["simple", "rebase git", "--json"], config=config)
    result = _json_result(completed)
    call = _calls(log)[-1]
    assert result["status"] == "success" and result["agent"] == "codex"
    assert result["profile"] == "simple" and result["model"] == "profile-model"
    assert result["exit_code"] == result["native_exit_code"] == completed.returncode == 0
    assert call == {
        "agent": "codex",
        "argv": [
            "exec",
            "--json",
            "--model",
            "profile-model",
            "-c",
            'model_reasoning_effort="medium"',
            "--sandbox",
            "read-only",
            "-",
        ],
        "stdin": "rebase git",
        "prompt": "rebase git",
        "cwd": str(consumer),
    }
    results["Q04"] = {"status": "Pass", "result": _compact_result(completed), "native": call}

    routed: dict[str, str] = {}
    for alias, agent in ALIASES.items():
        completed = _run(prat, root, [alias, f"route {alias}", "--json"], config=config)
        result = _json_result(completed)
        call = _calls(log)[-1]
        assert completed.returncode == result["exit_code"] == result["native_exit_code"] == 0
        assert result["status"] == "success" and result["error"] is None
        assert result["agent"] == call["agent"] == agent
        assert json.loads(result["output"])["prompt"] == f"route {alias}"
        assert call["prompt"] == f"route {alias}"
        routed[alias] = str(result["agent"])
    assert routed == ALIASES
    results["Q05"] = {"status": "Pass", "routed": routed}

    completed = _run(prat, root, ["cx", "missing config default", "--json"], path=fake_bin)
    result = _assert_result(
        completed, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    call = _calls(log)[-1]
    assert result["agent"] == "codex" and result["profile"] is None and result["model"] is None
    assert call["agent"] == "codex" and call["prompt"] == "missing config default"
    assert call["argv"] == ["exec", "--json", "-"]
    results["Q06"] = {"status": "Pass", "result": _compact_result(completed)}

    init_path = root / "init.toml"
    init_path.unlink(missing_ok=True)
    created = _run(prat, root, ["--config", str(init_path), "config", "init"])
    refused = _run(prat, root, ["--config", str(init_path), "config", "init"])
    validated = _run(prat, root, ["--config", str(init_path), "config", "validate"])
    profiles = _run(prat, root, ["--config", str(init_path), "profiles", "--json"])
    assert (created.returncode, refused.returncode, validated.returncode, profiles.returncode) == (
        0,
        2,
        0,
        0,
    )
    assert created.stdout.decode() == f"Created {init_path}\n"
    assert refused.stderr.decode() == (
        f"prat: {init_path}: already exists; edit it or choose another --config path.\n"
    )
    assert validated.stdout.decode() == f"Valid config: {init_path}\n"
    expected_profiles = {
        "schema_version": 1,
        "profiles": [
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
        ],
    }
    assert json.loads(profiles.stdout) == expected_profiles
    results["Q07"] = {
        "status": "Pass",
        "init_exit": 0,
        "refusal_exit": 2,
        "refusal": refused.stderr.decode().strip(),
        "validate_exit": 0,
        "profiles": expected_profiles["profiles"],
    }

    override_arguments = [
        "override",
        "precedence",
        "--model",
        "cli-model",
        "--effort",
        "high",
        "--timeout",
        "3",
        "--max-budget-usd",
        "4",
        "--max-turns",
        "5",
        "--json",
        "--",
        "--permission-mode",
        "plan",
    ]
    completed = _run(prat, root, override_arguments, config=config)
    call = _calls(log)[-1]
    result = _assert_result(
        completed, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    expected_native_argv = [
        "-p",
        "--output-format",
        "json",
        "--model",
        "cli-model",
        "--effort",
        "high",
        "--max-budget-usd",
        "4.0",
        "--max-turns",
        "5",
        "--permission-mode",
        "plan",
    ]
    assert result["agent"] == "claude" and result["profile"] == "override"
    assert result["model"] == "cli-model"
    assert call == {
        "agent": "claude",
        "argv": expected_native_argv,
        "stdin": "precedence",
        "prompt": "precedence",
        "cwd": str(consumer),
    }
    calls_before = len(_calls(log))
    dry_override = _run(
        prat, root, [*override_arguments[:-3], "--dry-run", *override_arguments[-3:]], config=config
    )
    dry_override_result = _json_result(dry_override)
    assert dry_override.returncode == 0
    assert dry_override_result == {
        "schema_version": 1,
        "dry_run": True,
        "agent": "claude",
        "profile": "override",
        "model": "cli-model",
        "fast": None,
        "argv": [str(python), str(script), "--fake-native", "claude", *expected_native_argv],
        "cwd": str(consumer),
        "timeout": 3.0,
        "stdin_bytes": len(b"precedence"),
    }
    assert len(_calls(log)) == calls_before
    results["Q08"] = {
        "status": "Pass",
        "native": call,
        "resolved": dry_override_result,
    }

    literal = "-leading $HOME $(touch injected) ; ✓\nsecond line"
    completed = _run(prat, root, ["cx", "-", "--json"], stdin=literal.encode(), config=config)
    call = _calls(log)[-1]
    result = _assert_result(
        completed, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    assert (
        result["agent"] == "codex"
        and call["prompt"] == call["stdin"] == literal
        and not (consumer / "injected").exists()
    )
    results["Q09"] = {"status": "Pass", "prompt": call["prompt"], "side_effect": False}

    calls_before = len(_calls(log))
    dry_prompt = "DRY_$HOME_$(touch dry-injected)_ß"
    argv_preview = _run(prat, root, ["gm", dry_prompt, "--dry-run", "--json"], config=config)
    stdin_preview = _run(prat, root, ["cx", dry_prompt, "--dry-run", "--json"], config=config)
    argv_json, stdin_json = _json_result(argv_preview), _json_result(stdin_preview)
    assert argv_preview.returncode == stdin_preview.returncode == 0
    assert argv_json["dry_run"] is stdin_json["dry_run"] is True
    assert argv_json["agent"] == "gemini" and stdin_json["agent"] == "codex"
    assert argv_json["timeout"] == stdin_json["timeout"] == 7.0
    assert argv_json["argv"][-1] == f"--prompt={dry_prompt}" and argv_json["stdin_bytes"] == 0
    assert stdin_json["argv"][-1] == "-" and stdin_json["stdin_bytes"] == len(dry_prompt.encode())
    assert len(_calls(log)) == calls_before and not (consumer / "dry-injected").exists()
    results["Q10"] = {
        "status": "Pass",
        "argv_transport": argv_json,
        "stdin_transport": stdin_json,
        "native_launches": 0,
        "side_effect": False,
    }

    success = _run(prat, root, ["cx", "json success", "--json"], config=config)
    failure = _run(prat, root, ["cx", "QA_NATIVE_17", "--json"], config=config)
    success_result = _assert_result(
        success, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    failure_result = _assert_result(
        failure,
        returncode=17,
        status="error",
        native_exit_code=17,
        error_code="native_exit",
        error_message="Codex exited with status 17.",
    )
    assert json.loads(success_result["output"])["prompt"] == "json success"
    assert failure_result["output"] == "native failure answer"
    results["Q11"] = {
        "status": "Pass",
        "success": _compact_result(success),
        "failure": _compact_result(failure),
    }

    invalid = root / "invalid.toml"
    invalid.write_text("version = 1\nunknown = true\n", encoding="utf-8")
    unsupported = root / "unsupported.toml"
    unsupported.write_text(
        'version = 1\n[profiles.bad]\nagent = "gemini"\neffort = "high"\n', encoding="utf-8"
    )
    calls_before = len(_calls(log))
    bad_cases = [
        _run(prat, root, ["cx", "prompt", "--json"], config=invalid),
        _run(prat, root, ["bad", "prompt", "--json"], config=unsupported),
        _run(prat, root, ["unknown", "prompt", "--json"], config=config),
    ]
    bad_results = [
        _assert_result(
            bad_cases[0],
            returncode=2,
            status="error",
            native_exit_code=None,
            error_code="invalid_config",
            error_message=f"{invalid}: unknown field 'unknown'.",
        ),
        _assert_result(
            bad_cases[1],
            returncode=2,
            status="error",
            native_exit_code=None,
            error_code="invalid_config",
            error_message=(
                f"{unsupported}: profiles.bad.effort: Gemini does not support an effort override."
            ),
        ),
        _assert_result(
            bad_cases[2],
            returncode=2,
            status="error",
            native_exit_code=None,
            error_code="invalid_arguments",
            error_message=(
                "Unknown selector 'unknown'; run 'prat agents' or 'prat profiles' to list choices."
            ),
        ),
    ]
    assert len(_calls(log)) == calls_before
    results["Q12"] = {
        "status": "Pass",
        "errors": [_compact_result(case) for case in bad_cases],
        "native_launches": 0,
    }
    assert all(result["output"] == "" for result in bad_results)

    unavailable = root / "unavailable.toml"
    missing = root / "missing-agent"
    blocked = root / "blocked-agent"
    blocked.write_text("plain text\n", encoding="utf-8")
    unavailable.write_text(
        f"version = 1\n[agents.codex]\ncommand = [{json.dumps(str(missing))}]\n",
        encoding="utf-8",
    )
    missing_case = _run(prat, root, ["cx", "missing", "--json"], config=unavailable)
    unavailable.write_text(
        f"version = 1\n[agents.codex]\ncommand = [{json.dumps(str(blocked))}]\n",
        encoding="utf-8",
    )
    blocked_case = _run(prat, root, ["cx", "blocked", "--json"], config=unavailable)
    _assert_result(
        missing_case,
        returncode=127,
        status="error",
        native_exit_code=None,
        error_code="executable_not_found",
        error_message=f"Executable '{missing}' was not found.",
    )
    _assert_result(
        blocked_case,
        returncode=126,
        status="error",
        native_exit_code=None,
        error_code="executable_not_executable",
        error_message=f"Executable '{blocked}' cannot be executed.",
    )
    results["Q13"] = {
        "status": "Pass",
        "missing": _compact_result(missing_case),
        "non_executable": _compact_result(blocked_case),
    }

    provider = _run(prat, root, ["cc", "QA_PROVIDER_FAILURE", "--json"], config=config)
    truncated = _run(prat, root, ["cx", "QA_TRUNCATED", "--json"], config=config)
    recovery = _run(prat, root, ["cc", "recovered", "--json"], config=config)
    provider_result = _assert_result(
        provider,
        returncode=1,
        status="error",
        native_exit_code=0,
        error_code="provider_error",
        error_message="controlled provider failure",
    )
    truncated_result = _assert_result(
        truncated,
        returncode=1,
        status="error",
        native_exit_code=0,
        error_code="protocol_error",
        error_message="Codex stream ended without turn.completed.",
    )
    recovery_result = _assert_result(
        recovery, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    assert provider_result["output"] == truncated_result["output"] == ""
    assert json.loads(recovery_result["output"])["prompt"] == "recovered"
    results["Q14"] = {
        "status": "Pass",
        "provider": _compact_result(provider),
        "truncated": _compact_result(truncated),
        "recovery": _compact_result(recovery),
    }

    timeout_fixture = _async_run(prat, root, config, "QA_TIMEOUT", "1")
    timeout_case, timeout_pending, timeout_released = _complete_async(timeout_fixture)
    interrupt_fixture = _async_run(prat, root, config, "QA_INTERRUPT", "9")
    interrupt_case, interrupt_pending, interrupt_released = _complete_async(
        interrupt_fixture, interrupt=True
    )
    assert timeout_pending and interrupt_pending
    assert timeout_released and interrupt_released
    _assert_result(
        timeout_case,
        returncode=124,
        status="timeout",
        native_exit_code=-signal.SIGTERM,
        error_code="timeout",
        error_message="Agent exceeded the 1 second timeout.",
    )
    _assert_result(
        interrupt_case,
        returncode=130,
        status="interrupted",
        native_exit_code=-signal.SIGTERM,
        error_code="interrupted",
        error_message="Interrupted by signal 2.",
    )
    retry = _run(prat, root, ["cx", "after interruption", "--json"], config=config)
    retry_result = _assert_result(
        retry, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    assert json.loads(retry_result["output"])["prompt"] == "after interruption"
    results["Q15"] = {
        "status": "Pass",
        "timeout": _compact_result(timeout_case),
        "interrupt": _compact_result(interrupt_case),
        "pending_observed": True,
        "descendant_locks_released": True,
        "recovery": _compact_result(retry),
    }

    pipe_fixture = _async_run(prat, root, config, "QA_PIPE_HOLDER", "1")
    pipe_case, pipe_pending, pipe_released = _complete_async(pipe_fixture)
    assert pipe_pending and pipe_released
    _assert_result(
        pipe_case,
        returncode=124,
        status="timeout",
        native_exit_code=0,
        error_code="timeout",
        error_message="Agent exceeded the 1 second timeout.",
    )
    results["Q16"] = {
        "status": "Pass",
        "result": _compact_result(pipe_case),
        "pending_observed": True,
        "descendant_lock_released": True,
    }

    limit_fixture = _async_run(prat, root, config, "QA_STDOUT_LIMIT", "9")
    limit_case, limit_pending, limit_released = _complete_async(limit_fixture)
    limit_result = _assert_result(
        limit_case,
        returncode=1,
        status="error",
        native_exit_code=-signal.SIGTERM,
        error_code="stdout_limit_exceeded",
        error_message="Agent stdout exceeded 8388608 bytes.",
    )
    stderr_fixture = _async_run(prat, root, config, "QA_STDERR_LIMIT", "9")
    stderr_case, stderr_pending, stderr_released = _complete_async(stderr_fixture)
    stderr_result = _assert_result(
        stderr_case,
        returncode=1,
        status="error",
        native_exit_code=-signal.SIGTERM,
        error_code="stderr_limit_exceeded",
        error_message="Agent stderr exceeded 2097152 bytes.",
    )
    invalid_utf8 = _run(prat, root, ["ki", "QA_BAD_UTF8", "--json"], config=config)
    oversized = _run(
        prat,
        root,
        ["cx", "-", "--json"],
        stdin=b"p" * (1024 * 1024 + 1),
        config=config,
    )
    invalid_utf8_result = _assert_result(
        invalid_utf8,
        returncode=1,
        status="error",
        native_exit_code=0,
        error_code="output_encoding",
        error_message="Agent stdout is not valid UTF-8.",
    )
    oversized_result = _assert_result(
        oversized,
        returncode=2,
        status="error",
        native_exit_code=None,
        error_code="invalid_arguments",
        error_message="Prompt exceeds the 1048576 byte limit.",
    )
    assert limit_pending and limit_released and limit_result["output"] == ""
    assert stderr_pending and stderr_released and stderr_result["output"] == ""
    assert invalid_utf8_result["output"] == oversized_result["output"] == ""
    results["Q17"] = {
        "status": "Pass",
        "stdout_limit": {
            "returncode": limit_case.returncode,
            "status": limit_result["status"],
            "error": limit_result["error"],
            "captured_json_bytes": len(limit_case.stdout),
            "captured_json_sha256": hashlib.sha256(limit_case.stdout).hexdigest(),
        },
        "stdout_pending_observed": limit_pending,
        "stdout_descendant_lock_released": limit_released,
        "stderr_limit": {
            "returncode": stderr_case.returncode,
            "status": stderr_result["status"],
            "error": stderr_result["error"],
            "captured_stderr_bytes": len(stderr_case.stderr),
            "captured_stderr_sha256": hashlib.sha256(stderr_case.stderr).hexdigest(),
        },
        "stderr_pending_observed": stderr_pending,
        "stderr_descendant_lock_released": stderr_released,
        "invalid_utf8": _compact_result(invalid_utf8),
        "oversized_prompt": _compact_result(oversized),
    }

    repository = Path(__file__).resolve().parents[1]
    runtime_identity = _runtime_identity(repository, prat, sdist_prat, wheel, sdist)
    runtime_diff = subprocess.check_output(
        ["git", "diff", "--binary", base_revision, "--", "src/pratfall"],
        cwd=repository,
        timeout=HARNESS_TIMEOUT,
    )
    evidence = {
        "schema_version": 1,
        "identity": {
            "platform": platform.platform(),
            "python": subprocess.check_output(
                [str(python), "--version"], text=True, timeout=HARNESS_TIMEOUT
            ).strip(),
            "base_revision": base_revision,
            "runtime_diff_sha256": hashlib.sha256(runtime_diff).hexdigest(),
            "wheel_sha256": _sha256(wheel),
            "sdist_sha256": _sha256(sdist),
            "wheel_prat": str(prat),
            "sdist_prat": str(sdist_prat),
            "script_sha256": _sha256(script),
            **runtime_identity,
        },
        "scenarios": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    portable = _portable(evidence, repository)
    output.write_text(json.dumps(portable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS {len(results)} scenarios; evidence: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-native")
    parser.add_argument("--lock-holder", nargs=2, type=Path)
    parser.add_argument("--prat", type=Path)
    parser.add_argument("--sdist-prat", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--base-revision")
    parser.add_argument("--expected-version")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args, native_arguments = parser.parse_known_args()
    if args.fake_native:
        return _fake_native(args.fake_native, native_arguments)
    if args.lock_holder:
        return _lock_holder(*args.lock_holder)
    if not all(
        (
            args.prat,
            args.sdist_prat,
            args.wheel,
            args.sdist,
            args.base_revision,
            args.expected_version,
            args.work_dir,
            args.output,
        )
    ):
        parser.error("all artifact, executable, revision, work-dir, and output flags are required")
    return _exercise(
        args.prat.resolve(),
        args.sdist_prat.resolve(),
        args.wheel.resolve(),
        args.sdist.resolve(),
        args.base_revision,
        args.work_dir.resolve(),
        args.output.resolve(),
        args.expected_version,
    )


if __name__ == "__main__":
    raise SystemExit(main())
