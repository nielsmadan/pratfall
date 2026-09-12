import fcntl
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

import pratfall.runner as runner_module
from pratfall.adapters import codex
from pratfall.consumer import ConsumerLimits
from pratfall.models import DecodedOutput, Invocation, ResultError
from pratfall.runner import STDERR_LIMIT, STDOUT_LIMIT, OutputLimits, run


def test_cleanup_rechecks_group_after_transient_probe_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []

    def killpg(group: int, chosen: int) -> None:
        assert group == 123
        signals.append(chosen)
        if chosen == 0:
            if signals.count(0) == 1:
                raise PermissionError(1, "Operation not permitted")
            raise ProcessLookupError

    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    assert runner_module.cleanup_process_group(123) is None
    assert signals == [signal.SIGTERM, 0, 0, 0]


def test_cleanup_reports_persistent_probe_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []

    def killpg(group: int, chosen: int) -> None:
        assert group == 123
        signals.append(chosen)
        if chosen == 0:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(runner_module, "TERMINATE_GRACE", 0)
    monkeypatch.setattr(runner_module, "FINAL_DRAIN_GRACE", 0)
    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    assert runner_module.cleanup_process_group(123) == (
        "could not verify owned process-group cleanup before the deadline"
    )
    assert signals == [signal.SIGTERM, 0, signal.SIGKILL, 0]


@pytest.mark.parametrize("denied", [signal.SIGTERM, signal.SIGKILL])
def test_cleanup_preserves_signal_permission_failure(
    monkeypatch: pytest.MonkeyPatch, denied: signal.Signals
) -> None:
    signals: list[int] = []

    def killpg(group: int, chosen: int) -> None:
        assert group == 123
        signals.append(chosen)
        if chosen in (0, denied):
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(runner_module, "TERMINATE_GRACE", 0)
    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    assert runner_module.cleanup_process_group(123) == "[Errno 1] Operation not permitted"
    assert signals == (
        [signal.SIGTERM] if denied == signal.SIGTERM else [signal.SIGTERM, 0, signal.SIGKILL]
    )


@pytest.mark.parametrize(
    ("denied_signal", "persistent_probe", "cleanup_error"),
    [
        (None, False, None),
        (None, True, "could not verify owned process-group cleanup before the deadline"),
        (signal.SIGTERM, False, "[Errno 1] Operation not permitted"),
        (signal.SIGKILL, False, "[Errno 1] Operation not permitted"),
    ],
)
def test_timeout_verifies_cleanup_after_parent_exits_and_pipes_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied_signal: signal.Signals | None,
    persistent_probe: bool,
    cleanup_error: str | None,
) -> None:
    original_drain = runner_module._drain
    signals: list[int] = []

    def drain_until_timeout(
        process: subprocess.Popen[bytes],
        selector: selectors.BaseSelector,
        output: runner_module._OutputState,
        deadline: float,
        context: runner_module._RunContext,
    ) -> tuple[ResultError | None, bool]:
        assert original_drain(process, selector, output, deadline, context) == (None, False)
        assert process.poll() == 0
        assert not selector.get_map()
        return None, True

    def killpg(_group: int, chosen: int) -> None:
        signals.append(chosen)
        if chosen == denied_signal:
            raise PermissionError(1, "Operation not permitted")
        if chosen == 0:
            if persistent_probe or signals.count(0) == 1:
                raise PermissionError(1, "Operation not permitted")
            raise ProcessLookupError

    monkeypatch.setattr(runner_module, "_drain", drain_until_timeout)
    monkeypatch.setattr(runner_module, "TERMINATE_GRACE", 0)
    monkeypatch.setattr(runner_module, "FINAL_DRAIN_GRACE", 0.15)
    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    result = run(python("pass"), tmp_path, 5)

    message = "Agent exceeded the 5 second timeout."
    if cleanup_error is not None:
        message += f" Process-group cleanup failed: {cleanup_error}."
    assert result.error == ResultError("timeout", message)
    assert result.timed_out is True
    assert result.native_exit_code == 0
    assert result.duration_ms < 1_000
    assert signals[0] == signal.SIGTERM
    assert signal.SIGKILL in signals
    if denied_signal is None:
        assert signals.count(0) >= (1 if persistent_probe else 2)


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


def test_timeout_does_not_wait_unused_term_grace_after_group_exits(tmp_path: Path) -> None:
    executable = tmp_path / "prompt-exit"
    executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    executable.chmod(0o755)
    result = run(Invocation((str(executable),), b""), tmp_path, 0.1)
    assert result.timed_out is True
    assert result.native_exit_code == -signal.SIGTERM
    assert result.duration_ms < 1_500


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


def test_runner_streams_stdout_to_consumer_without_retaining_trace(tmp_path: Path) -> None:
    stream = (
        '{"type":"item.completed","item":{"id":"answer","type":"agent_message",'
        '"text":"done"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":1,'
        '"cached_input_tokens":0,"output_tokens":2}}\n'
    )
    result = run(
        python(f"import os;os.write(1,{stream.encode()!r})"),
        tmp_path,
        5,
        consumer=codex.consumer(),
    )
    assert result.stdout == b""
    assert result.decoded is not None
    assert result.decoded.output == "done"
    assert result.error is None


def test_runner_finishes_consumer_once_after_stdout_closes(tmp_path: Path) -> None:
    class CountingConsumer:
        def __init__(self) -> None:
            self.feeds: list[bytes] = []
            self.finishes = 0

        def feed(self, data: bytes) -> str | None:
            self.feeds.append(data)
            return None

        def finish(self) -> DecodedOutput:
            self.finishes += 1
            return DecodedOutput(output=b"".join(self.feeds).decode())

    consumer = CountingConsumer()
    result = run(python("print('answer',end='')"), tmp_path, 5, consumer=consumer)
    assert consumer.finishes == 1
    assert result.stdout == b""
    assert result.decoded == DecodedOutput(output="answer")


def test_finish_time_limit_cleans_descendant_and_preserves_decoded_output(tmp_path: Path) -> None:
    lock_path = tmp_path / "stream-descendant.lock"
    pid_path = tmp_path / "stream-descendant.pid"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "open(sys.argv[3],'w').write(str(os.getpid()));"
        "os.close(1);os.close(2);os.write(int(sys.argv[1]),b'1');"
        "os.close(int(sys.argv[1]));time.sleep(30)"
    )
    answer = (
        b'{"type":"item.completed","item":{"id":"a","type":"agent_message","text":"partial"}}\n'
    )
    code = (
        "import os,subprocess,sys;"
        "ready_read,ready_write=os.pipe();"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r},str(ready_write),"
        f"{str(lock_path)!r},{str(pid_path)!r}],pass_fds=(ready_write,));"
        "os.close(ready_write);os.read(ready_read,1);os.close(ready_read);"
        f"os.write(1,{answer!r}+b'x'*256+b'\\r')"
    )
    try:
        result = run(
            python(code),
            tmp_path,
            5,
            consumer=codex.consumer(ConsumerLimits(event_bytes=256, state_bytes=1024, records=10)),
        )
        assert result.error is not None
        assert result.error.code == "stdout_limit_exceeded"
        assert result.decoded is not None
        assert result.decoded.output == "partial"
        assert result.stdout == b""
        assert_process_terminated(int(pid_path.read_text()), lock_path)
    finally:
        if pid_path.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_path.read_text()), signal.SIGKILL)


def test_missing_terminal_limit_outranks_native_exit_and_cleans_descendant(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "missing-terminal-descendant.lock"
    pid_path = tmp_path / "missing-terminal-descendant.pid"
    descendant = (
        "import fcntl,os,sys,time;"
        "lock=open(sys.argv[2],'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        "open(sys.argv[3],'w').write(str(os.getpid()));"
        "os.close(1);os.close(2);os.write(int(sys.argv[1]),b'1');"
        "os.close(int(sys.argv[1]));time.sleep(30)"
    )
    answer = (
        b'{"type":"item.completed","item":{"id":"","type":"agent_message","text":"answer12345"}}\n'
    )
    code = (
        "import os,subprocess,sys;"
        "ready_read,ready_write=os.pipe();"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r},str(ready_write),"
        f"{str(lock_path)!r},{str(pid_path)!r}],pass_fds=(ready_write,));"
        "os.close(ready_write);os.read(ready_read,1);os.close(ready_read);"
        f"os.write(1,{answer!r});sys.exit(17)"
    )
    try:
        result = run(
            python(code),
            tmp_path,
            5,
            consumer=codex.consumer(ConsumerLimits(event_bytes=256, state_bytes=11, records=10)),
        )
        assert result.error == ResultError(
            "stdout_limit_exceeded", "Agent retained output state exceeded 11 bytes."
        )
        assert result.native_exit_code == 17
        assert result.decoded is not None
        assert result.decoded.output == "answer12345"
        assert_process_terminated(int(pid_path.read_text()), lock_path)
    finally:
        if pid_path.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_path.read_text()), signal.SIGKILL)


def test_progress_failure_terminates_child_and_preserves_streamed_answer(tmp_path: Path) -> None:
    lock_path = tmp_path / "progress-child.lock"
    pid_path = tmp_path / "progress-child.pid"
    code = (
        "import fcntl,json,time;"
        f"lock=open({str(lock_path)!r},'wb');fcntl.flock(lock,fcntl.LOCK_EX);"
        f"open({str(pid_path)!r},'w').write(str(__import__('os').getpid()));"
        "print(json.dumps({'type':'item.completed','item':"
        "{'id':'a','type':'agent_message','text':'partial'}}),flush=True);time.sleep(30)"
    )
    calls = 0

    def fail_progress(_elapsed: int, _category: str) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise BrokenPipeError("closed progress pipe")

    try:
        result = run(
            python(code),
            tmp_path,
            10,
            consumer=codex.consumer(),
            progress=fail_progress,
        )
        assert result.error is not None
        assert result.error.code == "output_io_error"
        assert result.decoded is not None
        assert result.decoded.output == "partial"
        assert_process_terminated(int(pid_path.read_text()), lock_path)
    finally:
        if pid_path.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_path.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("interrupt", [False, True])
def test_timeout_and_interruption_feed_term_cleanup_bytes_to_consumer(
    tmp_path: Path, interrupt: bool
) -> None:
    ready = tmp_path / "cleanup-bytes-ready"
    payload = (
        b'{"type":"item.completed","item":{"id":"a","type":"agent_message",'
        b'"text":"cleanup answer"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":1,'
        b'"cached_input_tokens":0,"output_tokens":1}}\n'
    )
    code = (
        "import os,signal,time;"
        f"payload={payload!r};"
        "signal.signal(signal.SIGTERM,lambda *_:(os.write(1,payload),os._exit(0)));"
        f"open({str(ready)!r},'w').write('ready');"
        "time.sleep(30)"
    )
    sender: threading.Thread | None = None
    if interrupt:

        def send() -> None:
            while not ready.exists():
                time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGINT)

        sender = threading.Thread(target=send)
        sender.start()
    result = run(
        python(code),
        tmp_path,
        5 if interrupt else 0.2,
        consumer=codex.consumer(),
    )
    if sender is not None:
        sender.join()
    assert result.decoded is not None
    assert result.decoded.output == "cleanup answer"
    assert result.decoded.error is None
    if interrupt:
        assert result.interrupted_by == signal.SIGINT
    else:
        assert result.timed_out is True
