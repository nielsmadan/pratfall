import ast
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pratfall import catalog

SCRIPT = Path(__file__).with_name("qa_installed.py")
SPEC = importlib.util.spec_from_file_location("qa_installed", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
qa = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qa
SPEC.loader.exec_module(qa)


@pytest.mark.parametrize("optimization", ["flag", "environment"])
@pytest.mark.parametrize("mode", ["fake-native", "harness"])
def test_script_refuses_disabled_assertions_before_work(
    tmp_path: Path, optimization: str, mode: str
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONOPTIMIZE", None)
    environment["PRAT_QA_LOG"] = str(tmp_path / "native.jsonl")
    environment["PRAT_QA_OWNER"] = str(tmp_path / "owner.jsonl")
    command = [sys.executable]
    if optimization == "flag":
        command.append("-O")
    else:
        environment["PYTHONOPTIMIZE"] = "1"
    command.append(str(SCRIPT))
    if mode == "fake-native":
        command.extend(("--fake-native", "amp", "version"))
    else:
        for name in ("prat", "sdist-prat", "wheel", "sdist", "work-dir", "output"):
            command.extend((f"--{name}", str(tmp_path / name)))
        command.extend(("--base-revision", "HEAD", "--expected-version", "0.1.0"))
    completed = subprocess.run(
        command, input=b"", capture_output=True, check=False, timeout=5, env=environment
    )
    assert completed.returncode == 2
    assert completed.stderr == (
        b"qa_installed.py requires active assertions; run Python without -O or PYTHONOPTIMIZE.\n"
    )
    assert completed.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_ready_go_observes_pending_before_releasing_producer(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    lock_path = tmp_path / "handshake.lock"
    ready_path = tmp_path / "handshake.ready"
    go_path = tmp_path / "handshake.go"
    code = (
        "import fcntl,pathlib,sys,time;"
        "lock=pathlib.Path(sys.argv[1]);ready=pathlib.Path(sys.argv[2]);"
        "go=pathlib.Path(sys.argv[3]);stream=lock.open('wb');"
        "fcntl.flock(stream,fcntl.LOCK_EX);ready.write_text('ready');"
        "\nwhile not go.exists(): time.sleep(0.01)\n"
        "print('released')"
    )
    invocation = qa._start(
        [sys.executable, "-c", code, str(lock_path), str(ready_path), str(go_path)],
        tmp_path,
        os.environ.copy(),
    )
    fixture = qa._AsyncFixture(invocation, lock_path, ready_path, go_path)

    try:
        completed, pending, released = qa._complete_async(fixture)
    finally:
        if invocation.owner_path.exists() or invocation.process.poll() is None:
            qa._cleanup(invocation)

    assert completed.returncode == 0
    assert completed.stdout == b"released\n"
    assert pending is True
    assert released is True
    assert go_path.read_text() == "go\n"


def test_async_run_restricts_child_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "consumer").mkdir()
    (tmp_path / "bin").mkdir()
    ambient_bin = tmp_path / "ambient-bin"
    ambient_bin.mkdir()
    observed_path = tmp_path / "child-path.txt"
    prat = tmp_path / "prat"
    prat.write_text(
        f"#!{sys.executable}\n"
        "import fcntl, os, pathlib, sys, time\n"
        f"pathlib.Path({str(observed_path)!r}).write_text(os.environ['PATH'])\n"
        "controls = dict(\n"
        "    item.split('=', 1) for item in ' '.join(sys.argv[1:]).split() if '=' in item\n"
        ")\n"
        "lock = pathlib.Path(controls['LOCK'])\n"
        "ready = pathlib.Path(controls['READY'])\n"
        "go = pathlib.Path(controls['GO'])\n"
        "with lock.open('wb') as stream:\n"
        "    fcntl.flock(stream, fcntl.LOCK_EX)\n"
        "    ready.write_text('ready')\n"
        "    while not go.exists():\n"
        "        time.sleep(0.01)\n",
        encoding="utf-8",
    )
    prat.chmod(0o755)
    monkeypatch.setenv("PATH", f"{ambient_bin}{os.pathsep}/usr/bin{os.pathsep}/bin")

    fixture = qa._async_run(prat, tmp_path, tmp_path / "config.toml", "QA_PATH", "1")
    completed, pending, released = qa._complete_async(fixture)

    expected = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    assert completed.returncode == 0
    assert pending is True
    assert released is True
    assert observed_path.read_text() == expected
    assert str(ambient_bin) not in observed_path.read_text()


def test_completed_parent_reports_leaking_separate_descendant_before_cleanup(
    tmp_path: Path,
) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    lock_path = tmp_path / "leak.lock"
    ready_path = tmp_path / "leak.ready"
    go_path = tmp_path / "leak.go"
    code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,sys.argv[1],'--lock-holder',sys.argv[2],sys.argv[3]],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "start_new_session=True);"
        "go=sys.argv[4];"
        "\nwhile not __import__('pathlib').Path(go).exists(): time.sleep(0.01)\n"
    )
    invocation = qa._start(
        [
            sys.executable,
            "-c",
            code,
            str(SCRIPT),
            str(lock_path),
            str(ready_path),
            str(go_path),
        ],
        tmp_path,
        os.environ.copy(),
    )
    fixture = qa._AsyncFixture(invocation, lock_path, ready_path, go_path)

    try:
        completed, pending, released = qa._complete_async(fixture)

        assert completed.returncode == 0
        assert pending is True
        assert released is False
    finally:
        if invocation.owner_path.exists() or invocation.process.poll() is None:
            qa._cleanup(invocation)

    deadline = time.monotonic() + qa.CLEANUP_TIMEOUT
    while time.monotonic() < deadline and not qa._available(lock_path):
        time.sleep(0.01)
    assert qa._available(lock_path) is True


def test_deadline_cleans_separate_owned_fixture_group(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    lock_path = tmp_path / "deadline.lock"
    ready_path = tmp_path / "deadline.ready"
    code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,sys.argv[1],'--lock-holder',sys.argv[2],sys.argv[3]],"
        "start_new_session=True);time.sleep(30)"
    )
    invocation = qa._start(
        [sys.executable, "-c", code, str(SCRIPT), str(lock_path), str(ready_path)],
        tmp_path,
        os.environ.copy(),
    )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not ready_path.exists():
            time.sleep(0.01)
        assert ready_path.exists()
        assert qa._available(lock_path) is False

        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            qa._communicate(invocation, timeout=0.05)
    finally:
        if invocation.owner_path.exists() or invocation.process.poll() is None:
            qa._cleanup(invocation)

    assert time.monotonic() - started < 5
    assert invocation.process.poll() is not None
    assert qa._available(lock_path) is True
    assert invocation.owner_path.exists() is False


def test_result_check_rejects_false_pass() -> None:
    result = {
        "schema_version": 1,
        "status": "success",
        "exit_code": 0,
        "native_exit_code": 0,
        "reported_models": None,
        "cost_usd": None,
        "error": None,
    }
    completed = subprocess.CompletedProcess(["prat"], 0, json.dumps(result).encode() + b"\n", b"")
    assert (
        qa._assert_result(
            completed,
            returncode=0,
            status="success",
            native_exit_code=0,
            error_code=None,
        )
        == result
    )

    result["status"] = "error"
    completed.stdout = json.dumps(result).encode() + b"\n"
    with pytest.raises(AssertionError):
        qa._assert_result(
            completed,
            returncode=0,
            status="success",
            native_exit_code=0,
            error_code=None,
        )


def _input_error(message: str) -> subprocess.CompletedProcess[bytes]:
    result = {
        "schema_version": 1,
        "status": "error",
        "exit_code": 2,
        "native_exit_code": None,
        "reported_models": None,
        "cost_usd": None,
        "error": {"code": "invalid_arguments", "message": message},
    }
    return subprocess.CompletedProcess(["prat"], 2, json.dumps(result).encode() + b"\n", b"")


def test_input_failure_oracle_rejects_swapped_and_generic_messages() -> None:
    expected = "Provide exactly one prompt source."
    qa._assert_input_failure(_input_error(expected), expected)

    for wrong in (
        "Prompt exceeds the 1048576 byte limit.",
        "The prompt is invalid.",
    ):
        with pytest.raises(AssertionError):
            qa._assert_input_failure(_input_error(wrong), expected)


def _doctor_version_result(message: str) -> subprocess.CompletedProcess[bytes]:
    result = {
        "agents": [
            {
                "agent": "codex",
                "available": True,
                "path": "/isolated/python",
                "version": None,
                "version_error": message,
            }
        ]
    }
    return subprocess.CompletedProcess(["prat"], 0, json.dumps(result).encode() + b"\n", b"")


def test_version_failure_oracle_rejects_swapped_and_generic_diagnostics() -> None:
    timeout = qa._VERSION_CASE_ERRORS["QA_VERSION_TIMEOUT"]
    overflow = qa._VERSION_CASE_ERRORS["QA_VERSION_OVERFLOW"]
    qa._assert_version_cleanup_case(
        _doctor_version_result(timeout),
        "QA_VERSION_TIMEOUT",
        pending=True,
        released=True,
    )

    for wrong in (overflow, "Version probe failed."):
        with pytest.raises(AssertionError):
            qa._assert_version_cleanup_case(
                _doctor_version_result(wrong),
                "QA_VERSION_TIMEOUT",
                pending=True,
                released=True,
            )


def _input_entry_point(path: Path) -> None:
    path.write_text(
        "import json, signal\n"
        "from pathlib import Path\n"
        "def interrupted(signum, _frame):\n"
        f"    assert list(Path({str(path.parent)!r}).glob('input-signal-*.ready'))\n"
        '    print(json.dumps({"signal": signum}), flush=True)\n'
        "    raise SystemExit(128 + signum)\n"
        'interrupted.__module__ = "pratfall.prompt_input"\n'
        "signal.signal(signal.SIGTERM, interrupted)\n"
        "signal.pause()\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    path.with_name("python").symlink_to(sys.executable)


def test_installed_entry_point_signal_waits_for_delayed_input_readiness(tmp_path: Path) -> None:
    (tmp_path / "consumer").mkdir()
    (tmp_path / "bin").mkdir()
    prat = tmp_path / "prat"
    _input_entry_point(prat)
    started = time.monotonic()

    completed, pending, calls_before = qa._stdin_pending_run(
        prat,
        tmp_path,
        tmp_path / "config.toml",
        [],
        signal.SIGTERM,
        startup_delay=0.25,
        readiness_timeout=2,
    )

    assert time.monotonic() - started >= 0.2
    assert completed.returncode == 143
    assert json.loads(completed.stdout) == {"signal": 15}
    assert pending is True
    assert calls_before == 0


def test_installed_entry_point_input_readiness_timeout_is_explicit(tmp_path: Path) -> None:
    (tmp_path / "consumer").mkdir()
    (tmp_path / "bin").mkdir()
    prat = tmp_path / "prat"
    prat.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    prat.chmod(0o755)
    prat.with_name("python").symlink_to(sys.executable)

    with pytest.raises(
        AssertionError,
        match="installed entry point did not publish input SIGTERM readiness",
    ):
        qa._stdin_pending_run(
            prat,
            tmp_path,
            tmp_path / "config.toml",
            [],
            signal.SIGTERM,
            readiness_timeout=0.05,
        )


def test_install_identity_rejects_source_or_archive_mismatch(tmp_path: Path) -> None:
    source_root = (tmp_path / "source" / "pratfall").resolve()
    installed_root = (tmp_path / "env" / "site-packages" / "pratfall").resolve()
    source_root.mkdir(parents=True)
    installed_root.mkdir(parents=True)
    expected = {"cli.py": "expected"}
    qa._assert_install_identity(
        source_root, installed_root, tmp_path / "env", expected, expected, expected
    )

    with pytest.raises(AssertionError):
        qa._assert_install_identity(
            source_root,
            installed_root,
            tmp_path / "env",
            expected,
            expected,
            {"cli.py": "different"},
        )
    with pytest.raises(AssertionError):
        qa._assert_install_identity(
            source_root,
            source_root,
            tmp_path,
            expected,
            expected,
            expected,
        )


def _version_executable(path: Path, version: str) -> Path:
    path.write_text(
        f"#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ['--version']\n"
        f"print('prat {version}')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_both_artifact_entry_points_accept_non_initial_expected_version(tmp_path: Path) -> None:
    (tmp_path / "consumer").mkdir()
    wheel = _version_executable(tmp_path / "wheel-prat", "2.3.4")
    sdist = _version_executable(tmp_path / "sdist-prat", "2.3.4")
    wheel_result, sdist_result = qa._entry_point_versions(wheel, sdist, tmp_path, "2.3.4")
    assert wheel_result.stdout == b"prat 2.3.4\n"
    assert sdist_result.stdout == b"prat 2.3.4\n"


def test_artifact_entry_point_version_mismatch_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "consumer").mkdir()
    wheel = _version_executable(tmp_path / "wheel-prat", "2.3.4")
    sdist = _version_executable(tmp_path / "sdist-prat", "2.3.3")
    with pytest.raises(AssertionError):
        qa._entry_point_versions(wheel, sdist, tmp_path, "2.3.4")


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        ("openhands", ["--headless", "--json", "--task=-雪\nnext"]),
        ("warp", ["agent", "run", "--output-format", "ndjson", "--prompt=-雪\nnext"]),
        ("iflow", ["--prompt=-雪\nnext"]),
    ],
)
def test_new_native_contracts_reject_incorrect_invocation_and_stdin(
    agent: str, arguments: list[str]
) -> None:
    assert qa._prompt(agent, arguments, b"") == "-雪\nnext"
    for wrong in (
        [*arguments[:-1], "--unverified", arguments[-1]],
        [*arguments[:-1], arguments[-1].split("=", 1)[0], "-雪\nnext"],
        [*arguments, "extra prompt"],
        [],
    ):
        with pytest.raises(AssertionError):
            qa._prompt(agent, wrong, b"")
    with pytest.raises(AssertionError):
        qa._prompt(agent, arguments, b"extra prompt")


def test_new_native_contracts_check_flag_arity() -> None:
    for agent, arguments in (
        ("openhands", ["--headless", "--json", "--override-with-envs=true", "--task=x"]),
        ("warp", ["agent", "run", "--output-format", "ndjson", "--model", "--prompt=x"]),
        ("iflow", ["--plan=true", "--prompt=x"]),
    ):
        with pytest.raises(AssertionError):
            qa._prompt(agent, arguments, b"")


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        ("qwen", ["--output-format", "stream-json"]),
        ("amp", ["--execute", "--stream-json"]),
        ("reasonix", ["run", "--output-format", "json"]),
        ("droid", ["exec", "--output-format", "json"]),
        (
            "kimi",
            [
                "--print",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--final-message-only",
            ],
        ),
        ("vibe", ["--prompt", "--output", "json"]),
        ("crush", ["run", "--quiet"]),
        ("cortex", ["exec", "--file", "-"]),
    ],
)
def test_stdin_native_contracts_reject_wrong_argv(agent: str, arguments: list[str]) -> None:
    prompt = "-雪\nnext\n"
    expected = prompt + "\n\n" if agent == "crush" else prompt
    assert qa._prompt(agent, arguments, prompt.encode()) == (
        prompt.strip() if agent in {"reasonix", "kimi", "vibe"} else expected
    )
    for wrong in (
        [],
        arguments[:-1],
        [*arguments, "extra"],
        [*arguments, "--prompt=x"],
        [*arguments, "--unknown"],
        [*arguments, "--cwd=x"],
    ):
        with pytest.raises(AssertionError):
            qa._prompt(agent, wrong, prompt.encode())
    with pytest.raises(AssertionError):
        qa._prompt(agent, arguments, b"")


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        ("qwen", ["--output-format", "stream-json", "--model"]),
        ("qwen", ["--output-format", "stream-json", "--debug=true"]),
        ("amp", ["--execute", "--stream-json", "--stream-json-thinking=true"]),
        ("droid", ["exec", "--output-format", "json", "--auto"]),
        (
            "kimi",
            [
                "--print",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--final-message-only",
                "--thinking=true",
            ],
        ),
        ("vibe", ["--prompt", "--output", "json", "--max-tokens"]),
        ("crush", ["run", "--quiet", "--verbose=true"]),
        ("cortex", ["--connection", "exec", "--file", "-"]),
        ("reasonix", ["run", "--output-format", "json", "--effort"]),
        ("reasonix", ["run", "--output-format", "json", "--show-thinking=true"]),
    ],
)
def test_stdin_native_contracts_check_arity(agent: str, arguments: list[str]) -> None:
    with pytest.raises(AssertionError):
        qa._prompt(agent, arguments, b"prompt")


def test_devin_native_contract_checks_print_delimiter_options_and_stdin() -> None:
    prompt = "-雪\n$HOME `id` $(touch forbidden)\n"
    argv = ["-p", "--model", "opus", "--permission-mode", "normal", "--", prompt]
    assert qa._prompt("devin", argv, b"") == prompt
    for wrong in (
        [],
        ["--", prompt],
        ["-p", prompt],
        ["-p", "--prompt-file", "-"],
        ["-p", "--model", "--", prompt],
        ["-p", "--unverified", "--", prompt],
        ["-p", "--respect-workspace-trust=false", "--", prompt],
        ["-p", "--", prompt, "extra"],
    ):
        with pytest.raises(AssertionError):
            qa._prompt("devin", wrong, b"")
    with pytest.raises(AssertionError):
        qa._prompt("devin", argv, b"extra prompt")


@pytest.mark.parametrize("flag", ["--cloud", "--github=secret", "--executor=x", "--remote=x"])
def test_cortex_native_contract_rejects_nonlocal_controls(flag: str) -> None:
    with pytest.raises(AssertionError):
        qa._prompt("cortex", [flag, "exec", "--file", "-"], b"prompt")


@pytest.mark.parametrize("prompt", ["task", "  café 雪\r\n\t "])
def test_crush_native_stdin_prompt_adds_two_newlines(prompt: str) -> None:
    assert qa._prompt("crush", ["run", "--quiet"], prompt.encode()) == prompt + "\n\n"


@pytest.mark.parametrize("prompt", ["--version", "--output"])
def test_fake_dispatch_preserves_native_delimiter_and_option_named_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("PRAT_QA_LOG", str(log))
    arguments = ["-p", "--", prompt]
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake-native", "devin", *arguments],
        input=b"",
        capture_output=True,
        check=False,
        timeout=5,
    )
    expected = {
        "agent": "devin",
        "argv": arguments,
        "stdin": "",
        "prompt": prompt,
        "cwd": os.getcwd(),
    }
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == expected
    assert qa._calls(log) == [expected]


@pytest.mark.parametrize("agent", qa.AGENTS)
def test_fake_dispatch_recognizes_complete_native_version_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("PRAT_QA_LOG", str(log))
    arguments = ["version"] if agent == "amp" else ["--version"]
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake-native", agent, *arguments],
        input=b"",
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert qa._calls(log) == [{"agent": agent, "argv": arguments, "version_probe": True}]
    if agent == "hermes":
        assert completed.returncode == 17
        assert completed.stderr == b"opaque version failure\n"
    else:
        assert completed.returncode == 0
        assert completed.stdout == f"{agent} opaque version 1.0\n".encode()


@pytest.mark.parametrize(
    "mode", ["QA_VERSION_TIMEOUT", "QA_VERSION_OVERFLOW", "QA_VERSION_INTERRUPT"]
)
def test_fake_dispatch_preserves_explicit_version_lifecycle_fixtures(
    tmp_path: Path, mode: str
) -> None:
    (tmp_path / "consumer").mkdir()
    (tmp_path / "bin").mkdir()
    prat = tmp_path / "prat"
    prat.write_text(
        f"#!{sys.executable}\nfrom pratfall.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    prat.chmod(0o755)
    completed, pending, released = qa._version_cleanup_run(
        prat,
        tmp_path,
        Path(sys.executable),
        SCRIPT.resolve(),
        mode,
        interrupt=mode == "QA_VERSION_INTERRUPT",
    )
    qa._assert_version_cleanup_case(completed, mode, pending=pending, released=released)
    assert qa._calls(tmp_path / "native.jsonl") == [
        {
            "agent": "codex",
            "argv": [
                mode,
                str(tmp_path / f"{mode.lower()}.lock"),
                str(tmp_path / f"{mode.lower()}.ready"),
                str(tmp_path / f"{mode.lower()}.go"),
                "--version",
            ],
            "version_probe": True,
        }
    ]


@pytest.fixture
def source_entry_point(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "consumer").mkdir()
    (tmp_path / "bin").mkdir()
    prat = tmp_path / "prat"
    prat.write_text(
        f"#!{sys.executable}\nfrom pratfall.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    prat.chmod(0o755)
    prat.with_name("python").symlink_to(sys.executable)
    config = tmp_path / "config.toml"
    qa._write_config(config, Path(sys.executable), SCRIPT.resolve())
    return prat, config


@pytest.mark.parametrize(
    ("agent", "arguments", "accounting"),
    [
        (
            "claude",
            ["-p", "--output-format", "json", "--model", "requested"],
            {
                "reported_models": ["claude-primary", "claude-helper"],
                "cost_usd": 0,
                "usage": {
                    "input_tokens": 3,
                    "cached_input_tokens": None,
                    "cache_write_input_tokens": None,
                    "output_tokens": 2,
                    "reasoning_output_tokens": None,
                },
            },
        ),
        (
            "gemini",
            ["--output-format", "json", "--model", "requested", "--prompt=QA_ACCOUNT_GEMINI"],
            {
                "reported_models": ["gemini-primary", "gemini-helper"],
                "cost_usd": None,
                "usage": {
                    "input_tokens": 6,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": None,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 2,
                },
            },
        ),
        (
            "copilot",
            ["--output-format=json", "--model=requested", "--prompt=QA_ACCOUNT_COPILOT"],
            {"reported_models": ["copilot-native"], "cost_usd": None, "usage": None},
        ),
        (
            "openclaw",
            [
                "agent",
                "exec",
                "--json",
                "--message-file",
                "-",
                "--model",
                "requested",
                "--timeout",
                "7",
            ],
            {
                "reported_models": ["provider/model"],
                "cost_usd": 1.25,
                "usage": {
                    "input_tokens": 3,
                    "cached_input_tokens": None,
                    "cache_write_input_tokens": None,
                    "output_tokens": 2,
                    "reasoning_output_tokens": None,
                },
            },
        ),
        (
            "opencode",
            ["run", "--format", "json", "--model", "requested"],
            {
                "reported_models": None,
                "cost_usd": 2.5,
                "usage": {
                    "input_tokens": 6,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 0,
                },
            },
        ),
    ],
)
def test_accounting_controls_through_fake_dispatch_and_source_entry_point(
    tmp_path: Path,
    source_entry_point: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    arguments: list[str],
    accounting: dict[str, object],
) -> None:
    prat, config = source_entry_point
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("PRAT_QA_LOG", str(log))
    prompt = f"QA_ACCOUNT_{agent.upper()}"
    stdin = b"" if agent in {"gemini", "copilot"} else prompt.encode()
    native = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake-native", agent, *arguments],
        input=stdin,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert native.returncode == 0, native.stderr.decode()
    assert b"ACCOUNT_OK" in native.stdout
    assert native.stderr == b""
    completed = qa._run(
        prat, tmp_path, [agent, prompt, "--model", "requested", "--json"], config=config
    )
    result = qa._assert_result(
        completed, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    assert result["agent"] == agent
    assert result["model"] == "requested"
    assert result["output"] == "ACCOUNT_OK"
    assert {field: result[field] for field in accounting} == accounting
    record = {"agent": agent, "argv": arguments, "stdin": stdin.decode(), "prompt": prompt}
    assert qa._calls(log) == [
        {**record, "cwd": os.getcwd()},
        {**record, "cwd": str(tmp_path / "consumer")},
    ]


@pytest.mark.parametrize(
    "prompt",
    ["QA_A", "QA_ACCOUNT_OTHER", "QA_A9.ordinary", "QA_A99 ordinary", "ordinary QA_A99.unknown"],
)
def test_a_tier_namespace_preserves_ordinary_fake_and_source_prompts(
    tmp_path: Path,
    source_entry_point: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    prat, config = source_entry_point
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("PRAT_QA_LOG", str(log))
    arguments = ["-p", "--", prompt]
    native = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake-native", "devin", *arguments],
        input=b"",
        capture_output=True,
        check=False,
        timeout=5,
    )
    record = {"agent": "devin", "argv": arguments, "stdin": "", "prompt": prompt}
    assert native.returncode == 0
    assert json.loads(native.stdout) == {**record, "cwd": os.getcwd()}
    completed = qa._run(prat, tmp_path, ["devin", prompt, "--json"], config=config)
    result = qa._assert_result(
        completed, returncode=0, status="success", native_exit_code=0, error_code=None
    )
    assert json.loads(result["output"]) == {**record, "cwd": str(tmp_path / "consumer")}
    assert qa._calls(log) == [
        {**record, "cwd": os.getcwd()},
        {**record, "cwd": str(tmp_path / "consumer")},
    ]


@pytest.mark.parametrize(
    ("agent", "prompt", "arguments"),
    [
        ("qwen", "QA_A99.unknown", ["--output-format", "stream-json"]),
        ("qwen", "QA_A09.qwen_failure_extra", ["--output-format", "stream-json"]),
        ("qwen", "QA_A09.vibe_projection", ["--output-format", "stream-json"]),
        ("qwen", "QA_A12.recovery", ["--output-format", "stream-json"]),
        ("vibe", "QA_A09.unknown", ["--prompt", "--output", "json"]),
        ("openhands", "QA_A12.unknown", ["--headless", "--json", "--task=QA_A12.unknown"]),
    ],
)
def test_fake_dispatch_rejects_unknown_or_wrong_agent_a_tier_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    prompt: str,
    arguments: list[str],
) -> None:
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("PRAT_QA_LOG", str(log))
    stdin = b"" if agent == "openhands" else prompt.encode()
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake-native", agent, *arguments],
        input=stdin,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr.decode().splitlines()[-1] == (
        f"ValueError: Unknown A-tier fixture for {agent}: {prompt!r}"
    )
    assert qa._calls(log) == [
        {
            "agent": agent,
            "argv": arguments,
            "stdin": stdin.decode(),
            "prompt": prompt,
            "cwd": os.getcwd(),
        }
    ]


def test_a_tier_protocol_cases_through_source_entry_point(
    tmp_path: Path, source_entry_point: tuple[Path, Path]
) -> None:
    prat, config = source_entry_point
    results = qa._exercise_a_protocols(prat, tmp_path, config)
    assert set(results) == {expected.case for expected in qa._PROTOCOL_EXPECTATIONS}
    assert len(results) == 25
    assert all(observation["status"] == "Pass" for observation in results.values())
    assert len(qa._calls(tmp_path / "native.jsonl")) == 25


def test_hostile_nesting_fixture_emits_the_full_bounded_document(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert qa._fake_a_tier("reasonix", "QA_A14.nesting") == 0
    assert capsys.readouterr().out == "[" * 10_000 + "0" + "]" * 10_000 + "\n"


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("output", "CHILD_TEXT"),
        ("reported_models", ["child-model"]),
        ("usage", {"input_tokens": 900, "output_tokens": 700}),
        ("cost_usd", 2.5),
        ("native_exit_code", 17),
    ],
)
def test_protocol_oracle_rejects_incorrect_retained_fields(field: str, wrong: object) -> None:
    expected = qa._ProtocolExpectation(
        "A10.recovery", "qwen", "QWEN_RECOVERED", tokens=(37, 11, 5), models=("root-model",)
    )
    result = {
        "schema_version": 1,
        "agent": "qwen",
        "profile": None,
        "model": None,
        "output": "QWEN_RECOVERED",
        "reported_models": ["root-model"],
        "cost_usd": None,
        "usage": {
            "input_tokens": 37,
            "cached_input_tokens": 5,
            "cache_write_input_tokens": None,
            "output_tokens": 11,
            "reasoning_output_tokens": None,
        },
        "status": "success",
        "exit_code": 0,
        "native_exit_code": 0,
        "error": None,
    }
    completed = subprocess.CompletedProcess(["prat"], 0, json.dumps(result).encode() + b"\n", b"")
    qa._assert_protocol_result(completed, expected)
    result[field] = wrong
    completed.stdout = json.dumps(result).encode() + b"\n"
    with pytest.raises(AssertionError):
        qa._assert_protocol_result(completed, expected)


@pytest.mark.parametrize(
    "exercise",
    ["_exercise_a_inventory", "_exercise_routes", "_exercise_versions", "_exercise_a_sources"],
)
def test_a_tier_contracts_through_source_entry_point(
    tmp_path: Path, source_entry_point: tuple[Path, Path], exercise: str
) -> None:
    prat, config = source_entry_point
    observation = getattr(qa, exercise)(prat, tmp_path, config)
    assert observation["status"] == "Pass"


def test_a_tier_profile_and_rejections_through_source_entry_point(
    tmp_path: Path, source_entry_point: tuple[Path, Path]
) -> None:
    prat, config = source_entry_point
    assert qa._exercise_a_profile(prat, tmp_path)["status"] == "Pass"
    observations = qa._exercise_a_rejections(prat, tmp_path, config)
    assert set(observations) == {"A06.controls", "A07.collisions"}
    assert all(observation["status"] == "Pass" for observation in observations.values())


def _harness_commands() -> dict[str, str]:
    commands: dict[str, str] = qa.AGENTS
    return commands


def _harness_aliases() -> dict[str, str]:
    aliases: dict[str, str] = qa.ALIASES
    return aliases


def _harness_version_args() -> dict[str, list[str]]:
    version_args: dict[str, list[str]] = qa.VERSION_ARGS
    return version_args


def _harness_effort_values() -> dict[str, list[str] | None]:
    effort_values: dict[str, list[str] | None] = qa._EFFORT_VALUES
    return effort_values


def _harness_budgets() -> dict[str, list[str]]:
    budgets: dict[str, list[str]] = qa._BUDGETS
    return budgets


def _harness_without_model() -> frozenset[str]:
    without_model: frozenset[str] = qa._NO_MODEL
    return without_model


def _harness_with_fast() -> frozenset[str]:
    with_fast: frozenset[str] = qa._FAST
    return with_fast


def _harness_names() -> list[str]:
    return list(_harness_commands())


def _harness_command_lists() -> dict[str, list[str]]:
    return {name: [command] for name, command in _harness_commands().items()}


def _harness_alias_lists() -> dict[str, list[str]]:
    owners = _harness_aliases()
    return {
        name: [alias for alias, owner in owners.items() if owner == name]
        for name in _harness_commands()
    }


def _catalog_names() -> list[str]:
    return [agent.name for agent in catalog.AGENTS]


def _catalog_command_lists() -> dict[str, list[str]]:
    return {agent.name: list(agent.command) for agent in catalog.AGENTS}


def _catalog_aliases() -> dict[str, str]:
    return {alias: agent.name for agent in catalog.AGENTS for alias in agent.aliases}


def _catalog_alias_lists() -> dict[str, list[str]]:
    return {agent.name: list(agent.aliases) for agent in catalog.AGENTS}


def _catalog_version_args() -> dict[str, list[str]]:
    return {agent.name: list(agent.version_args) for agent in catalog.AGENTS}


def _catalog_effort_values() -> dict[str, list[str] | None]:
    return {
        agent.name: list(agent.capabilities.effort_values) or None
        for agent in catalog.AGENTS
        if agent.capabilities.effort
    }


def _catalog_budgets() -> dict[str, list[str]]:
    return {
        agent.name: sorted(agent.capabilities.budgets)
        for agent in catalog.AGENTS
        if agent.capabilities.budgets
    }


def _catalog_without_model() -> frozenset[str]:
    return frozenset(agent.name for agent in catalog.AGENTS if not agent.capabilities.model)


def _catalog_with_fast() -> frozenset[str]:
    return frozenset(agent.name for agent in catalog.AGENTS if agent.capabilities.fast)


def test_release_harness_declares_its_catalog_without_importing_pratfall() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module)
    assert imported and not {name for name in imported if name.split(".")[0] == "pratfall"}
    assert _harness_names() and not hasattr(qa, "catalog")


def test_harness_agent_names_match_the_catalog() -> None:
    assert set(_harness_names()) == set(_catalog_names())
    assert _harness_names() == _catalog_names()


def test_harness_agent_commands_match_the_catalog() -> None:
    assert _harness_command_lists() == _catalog_command_lists()


def test_harness_aliases_match_the_catalog() -> None:
    assert set(_harness_aliases()) == set(_catalog_aliases())
    assert _harness_aliases() == _catalog_aliases()
    assert _harness_alias_lists() == _catalog_alias_lists()


def test_harness_version_arguments_match_the_catalog() -> None:
    assert _harness_version_args() == _catalog_version_args()


def test_harness_effort_values_match_the_catalog() -> None:
    assert set(_harness_effort_values()) == set(_catalog_effort_values())
    assert _harness_effort_values() == _catalog_effort_values()


def test_harness_budgets_match_the_catalog() -> None:
    assert set(_harness_budgets()) == set(_catalog_budgets())
    assert _harness_budgets() == _catalog_budgets()


def test_harness_model_and_fast_capabilities_match_the_catalog() -> None:
    assert _harness_without_model() == _catalog_without_model()
    assert _harness_with_fast() == _catalog_with_fast()
