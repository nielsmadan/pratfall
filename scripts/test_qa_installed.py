import importlib.util
import json
import os
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
