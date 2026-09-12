import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("qa_installed.py")
SPEC = importlib.util.spec_from_file_location("qa_installed", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
qa = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qa
SPEC.loader.exec_module(qa)


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
        "status": "error",
        "exit_code": 0,
        "native_exit_code": 0,
        "error": None,
    }
    completed = subprocess.CompletedProcess(["prat"], 0, json.dumps(result).encode() + b"\n", b"")

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
