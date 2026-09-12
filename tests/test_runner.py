import fcntl
import os
import signal
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

from pratfall.models import Invocation
from pratfall.runner import STDERR_LIMIT, STDOUT_LIMIT, OutputLimits, run


def python(code: str, stdin: bytes = b"") -> Invocation:
    return Invocation((sys.executable, "-c", code), stdin)


def assert_process_terminated(pid: int, lock_path: Path) -> None:
    deadline = time.monotonic() + 2
    with lock_path.open("a+b") as stream:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            fcntl.flock(stream, fcntl.LOCK_UN)
            return
    pytest.fail(f"descendant {pid} still holds its process-lifetime lock")


def test_runner_drains_large_stdout_and_stderr_concurrently(tmp_path: Path) -> None:
    size = 1024 * 1024
    code = (
        "import os,threading;"
        f"a=threading.Thread(target=lambda:os.write(1,b'a'*{size}));"
        f"b=threading.Thread(target=lambda:os.write(2,b'b'*{size}));"
        "a.start();b.start();a.join();b.join()"
    )
    result = run(python(code), tmp_path, 5)
    assert result.error is None
    assert result.native_exit_code == 0
    assert result.stdout == b"a" * size
    assert result.stderr == b"b" * size


def test_runner_uses_bounded_file_for_large_stdin(tmp_path: Path) -> None:
    prompt = b"p" * (1024 * 1024)
    result = run(python("import sys;print(len(sys.stdin.buffer.read()))", prompt), tmp_path, 5)
    assert result.error is None
    assert result.stdout == b"1048576\n"


@pytest.mark.parametrize(
    ("fd", "limit", "code"),
    [
        ("stdout", STDOUT_LIMIT, "import os;os.write(1,b'x'*(8*1024*1024+1))"),
        ("stderr", STDERR_LIMIT, "import os;os.write(2,b'x'*(2*1024*1024+1))"),
    ],
)
def test_output_bounds_fail_and_stop_child(tmp_path: Path, fd: str, limit: int, code: str) -> None:
    result = run(python(code), tmp_path, 5)
    assert result.error is not None
    assert result.error.code == f"{fd}_limit_exceeded"
    assert len(getattr(result, fd)) == limit
    assert result.duration_ms < 4_000


def test_runner_accepts_explicit_capture_bounds_without_changing_defaults(tmp_path: Path) -> None:
    limited = run(
        python("import os;os.write(1,b'12345')"),
        tmp_path,
        5,
        output_limits=OutputLimits(stdout=4, stderr=7),
    )
    assert limited.stdout == b"1234"
    assert limited.error is not None
    assert limited.error.code == "stdout_limit_exceeded"
    ordinary = run(python("import os;os.write(1,b'12345')"), tmp_path, 5)
    assert ordinary.stdout == b"12345"
    assert ordinary.error is None


def test_runner_preserves_failing_native_exit_and_output(tmp_path: Path) -> None:
    result = run(
        python("import os,sys;os.write(1,b'out');os.write(2,b'err');sys.exit(17)"),
        tmp_path,
        5,
    )
    assert result.error is None
    assert result.native_exit_code == 17
    assert result.stdout == b"out"
    assert result.stderr == b"err"


def test_missing_and_nonexecutable_commands_are_distinguished(tmp_path: Path) -> None:
    missing = run(Invocation((str(tmp_path / "missing"),), b""), tmp_path, 1)
    assert missing.error is not None
    assert missing.error.code == "executable_not_found"
    file = tmp_path / "file"
    file.write_text("plain text", encoding="utf-8")
    blocked = run(Invocation((str(file),), b""), tmp_path, 1)
    assert blocked.error is not None
    assert blocked.error.code == "executable_not_executable"


def test_deadline_covers_pipes_held_by_grandchild(tmp_path: Path) -> None:
    lock_path = tmp_path / "descendant.lock"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[1],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "print(os.getpid(),flush=True);time.sleep(30)"
    )
    code = (
        "import subprocess,sys;"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r},{str(lock_path)!r}])"
    )
    result = run(python(code), tmp_path, 0.3)
    descendant_pid = int(result.stdout)
    assert result.timed_out is True
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.native_exit_code == 0
    assert 250 <= result.duration_ms < 3_000
    try:
        assert_process_terminated(descendant_pid, lock_path)
    finally:
        with suppress(ProcessLookupError):
            os.kill(descendant_pid, signal.SIGKILL)


def test_timeout_still_kills_descendant_after_parent_exits_and_pipes_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "descendant.lock"
    descendant = (
        "import fcntl,os,signal,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "os.close(1);os.close(2);"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "ready=int(sys.argv[1]);os.write(ready,b'1');os.close(ready);"
        "time.sleep(30)"
    )
    code = (
        "import os,subprocess,sys,time;"
        "ready_read,ready_write=os.pipe();"
        f"child=subprocess.Popen([sys.executable,'-c',{descendant!r},str(ready_write),"
        f"{str(lock_path)!r}],"
        "pass_fds=(ready_write,));"
        "os.close(ready_write);os.read(ready_read,1);os.close(ready_read);"
        "print(child.pid,flush=True);time.sleep(30)"
    )
    result = run(python(code), tmp_path, 0.2)
    descendant_pid = int(result.stdout)
    assert result.timed_out is True
    assert result.native_exit_code == -signal.SIGTERM
    assert 2_100 <= result.duration_ms < 4_000
    try:
        assert_process_terminated(descendant_pid, lock_path)
    finally:
        with suppress(ProcessLookupError):
            os.kill(descendant_pid, signal.SIGKILL)


def test_timeout_kills_process_group_that_ignores_term(tmp_path: Path) -> None:
    code = (
        "import signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "subprocess.Popen([sys.executable,'-c',"
        "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']);"
        "time.sleep(30)"
    )
    result = run(python(code), tmp_path, 0.2)
    assert result.timed_out is True
    assert result.native_exit_code == -signal.SIGKILL
    assert 2_100 <= result.duration_ms < 4_000


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
def test_runner_converts_cancellation_and_restores_handler(
    tmp_path: Path, chosen: signal.Signals
) -> None:
    previous = signal.getsignal(chosen)

    def interrupt() -> None:
        time.sleep(0.15)
        os.kill(os.getpid(), chosen)

    sender = threading.Thread(target=interrupt)
    sender.start()
    result = run(python("import time;time.sleep(30)"), tmp_path, 5)
    sender.join()
    assert result.interrupted_by == chosen
    assert result.error is not None
    assert result.error.code == "interrupted"
    assert signal.getsignal(chosen) == previous


def test_repeated_interrupt_escalates_without_stranding_child(tmp_path: Path) -> None:
    def interrupt() -> None:
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGINT)

    sender = threading.Thread(target=interrupt)
    sender.start()
    result = run(
        python("import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"),
        tmp_path,
        5,
    )
    sender.join()
    assert result.interrupted_by == signal.SIGINT
    assert result.native_exit_code == -signal.SIGKILL
    assert result.duration_ms < 2_000
