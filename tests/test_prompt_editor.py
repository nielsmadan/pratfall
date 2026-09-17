import fcntl
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pratfall import prompt_editor
from pratfall.errors import PratError

_CONTROLLER = """
import fcntl, json, os, signal, sys, termios
from pathlib import Path
from pratfall.cli import main
from pratfall.prompt_editor import edit_prompt
from pratfall.prompt_input import InputInterrupted
from pratfall.errors import PratError
request = json.loads(Path(sys.argv[2]).read_text())
tty = os.open(sys.argv[1], os.O_RDWR)
fcntl.ioctl(tty, termios.TIOCSCTTY, 0)
attrs = termios.tcgetattr(tty)
foreground = os.tcgetpgrp(tty)
handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGTTOU)}
report = {}
if request.get('immediate'):
    import subprocess
    original_spawn = subprocess.Popen
    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        process.wait(timeout=3)
        return process
    subprocess.Popen = spawn
if request.get('race'):
    original = os.tcsetpgrp
    def transfer(fd, group):
        if group != foreground:
            pid, status = os.waitpid(group, os.WUNTRACED)
            report['startup_stop'] = os.WSTOPSIG(status)
        original(fd, group)
    os.tcsetpgrp = transfer
try:
    if 'args' in request:
        report['code'] = main(request['args'])
    else:
        try:
            report['value'] = edit_prompt(request.get('draft', 'draft').encode(), Path.cwd()).decode()
            report['code'] = 0
        except InputInterrupted as error:
            report.update(code=128 + error.signum, error=str(error))
        except PratError as error:
            report.update(code=error.exit_code, error=str(error))
finally:
    report['foreground_restored'] = os.tcgetpgrp(tty) == foreground
    report['attributes_restored'] = termios.tcgetattr(tty) == attrs
    report['handlers_restored'] = all(signal.getsignal(s) == h for s,h in handlers.items())
    os.write(tty, b'RESTORED\\n')
    Path('report.json').write_text(json.dumps(report))
"""

_EDITOR_PREFIX = """
import fcntl, json, os, signal, stat, subprocess, sys, termios, time
from pathlib import Path
path = Path(sys.argv[-1])
Path('editor.json').write_text(json.dumps(dict(
    argv=sys.argv[1:-1], draft=path.read_text(), path=str(path), cwd=os.getcwd(),
    tty=[os.isatty(fd) for fd in range(3)], foreground=os.tcgetpgrp(0) == os.getpgrp(),
    mode=stat.S_IMODE(path.stat().st_mode), parent_mode=stat.S_IMODE(path.parent.stat().st_mode),
    pid=os.getpid(), group=os.getpgrp(), session=os.getsid(0), parent_session=os.getsid(os.getppid())
)))
"""


@dataclass
class EditorSession:
    process: subprocess.Popen[bytes]
    master: int
    directory: Path

    def wait_for(self, marker: bytes) -> bytes:
        output = bytearray()
        deadline = time.monotonic() + 8
        while marker not in output and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                output.extend(os.read(self.master, 65536))
        assert marker in output, output
        return bytes(output)

    def finish(self) -> tuple[dict[str, Any], bytes, bytes]:
        self.wait_for(b"RESTORED")
        stdout, stderr = self.process.communicate(timeout=8)
        assert self.process.returncode == 0, stderr.decode()
        report = json.loads((self.directory / "report.json").read_text())
        assert report["foreground_restored"]
        assert report["attributes_restored"]
        assert report["handlers_restored"]
        assert list((self.directory / "temp").iterdir()) == []
        return report, stdout, stderr


@contextmanager
def editor_session(
    directory: Path,
    code: str,
    request: dict[str, object] | None = None,
    environment: dict[str, str] | None = None,
) -> Iterator[EditorSession]:
    script = directory / "fake editor.py"
    script.write_text(_EDITOR_PREFIX + code)
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request or {}))
    temporary = directory / "temp"
    temporary.mkdir()
    env = {
        **os.environ,
        "EDITOR": shlex.join([sys.executable, "./fake editor.py", "quoted argument", "$literal"]),
        "VISUAL": "",
        "TMPDIR": str(temporary),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        **(environment or {}),
    }
    master, slave = os.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", _CONTROLLER, os.ttyname(slave), str(request_path)],
        cwd=directory,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        yield EditorSession(process, master, directory)
    finally:
        os.close(master)
        os.close(slave)
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        editor_record = directory / "editor.json"
        if editor_record.exists():
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(json.loads(editor_record.read_text())["group"], signal.SIGKILL)


def test_foreground_editor_reads_terminal_and_atomic_replacement_restores_tty(
    tmp_path: Path,
) -> None:
    code = """
os.write(1, b'EDITOR READY\\n')
answer = os.read(0, 100)
settings = termios.tcgetattr(0)
settings[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, settings)
replacement = path.with_suffix('.new')
replacement.write_bytes(answer)
replacement.replace(path)
os.write(2, b'EDITOR DIAGNOSTIC\\n')
"""
    with editor_session(tmp_path, code) as session:
        session.wait_for(b"EDITOR READY")
        os.write(session.master, "edited café\n".encode())
        report, stdout, stderr = session.finish()
    assert report["value"] == "edited café\n"
    assert stdout == stderr == b""
    record = json.loads((tmp_path / "editor.json").read_text())
    assert record["argv"] == ["quoted argument", "$literal"]
    assert record["draft"] == "draft"
    assert record["tty"] == [True, True, True]
    assert record["foreground"]
    assert record["session"] == record["parent_session"]
    assert record["mode"] == 0o600
    assert record["parent_mode"] == 0o700
    assert not Path(record["path"]).exists()


def test_editor_startup_terminal_read_race(tmp_path: Path) -> None:
    code = "os.write(1, b'READING\\n')\npath.write_bytes(os.read(0, 100))\n"
    with editor_session(tmp_path, code, {"race": True}) as session:
        session.wait_for(b"READING")
        os.write(session.master, b"after stop\n")
        report, _, _ = session.finish()
    assert report["startup_stop"] == signal.SIGTTIN
    assert report["value"] == "after stop\n"


@pytest.mark.parametrize(
    ("visual", "editor", "expected"),
    [
        ("visual --flag", "other", ["visual", "--flag"]),
        (" \t", 'editor "a b"', ["editor", "a b"]),
        ("", " \n", ["vi"]),
    ],
)
def test_editor_environment_precedence(
    monkeypatch: pytest.MonkeyPatch, visual: str, editor: str, expected: list[str]
) -> None:
    monkeypatch.setenv("VISUAL", visual)
    monkeypatch.setenv("EDITOR", editor)
    assert prompt_editor._editor_command() == expected


@pytest.mark.parametrize("command", ['"', "''"])
def test_invalid_editor_command_is_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("VISUAL", command)
    with pytest.raises(PratError):
        prompt_editor.edit_prompt(b"draft", tmp_path)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("path.write_bytes(b'')", "empty"),
        ("path.write_bytes(b' \\n')", "whitespace"),
        ("path.write_bytes(b'\\xff')", "UTF-8"),
        ("path.write_bytes(b'a\\0b')", "NUL"),
        ("path.write_bytes(b'x' * (1048576 + 1))", "byte limit"),
        ("path.unlink()", "Cannot open"),
        ("path.unlink(); path.mkdir()", "regular file"),
        ("sys.exit(7)", "status 7"),
    ],
)
def test_invalid_edit_cleans_up_and_restores_terminal(
    tmp_path: Path, code: str, message: str
) -> None:
    with editor_session(tmp_path, code) as session:
        report, _, _ = session.finish()
    assert report["code"] == 2
    assert message in report["error"]


def test_editor_launch_failure_restores_terminal(tmp_path: Path) -> None:
    with editor_session(tmp_path, "", environment={"VISUAL": "/nonexistent/editor"}) as session:
        report, _, _ = session.finish()
    assert report["code"] == 2
    assert "Cannot edit prompt" in report["error"]


@pytest.mark.parametrize("chosen", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("target", ["parent", "editor"])
def test_editor_signal_normalization_and_terminal_restoration(
    tmp_path: Path, chosen: signal.Signals, target: str
) -> None:
    code = """
signal.signal(signal.SIGINT, signal.SIG_DFL)
settings = termios.tcgetattr(0)
settings[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, settings)
os.write(1, b'READY\\n')
while True: signal.pause()
"""
    with editor_session(tmp_path, code) as session:
        session.wait_for(b"READY")
        record = json.loads((tmp_path / "editor.json").read_text())
        os.kill(session.process.pid if target == "parent" else record["pid"], chosen)
        report, _, _ = session.finish()
    assert report["code"] == 128 + chosen


def test_terminal_ctrl_c_is_normalized(tmp_path: Path) -> None:
    code = "signal.signal(signal.SIGINT, signal.SIG_DFL)\nos.write(1, b'READY\\n')\nos.read(0, 10)"
    with editor_session(tmp_path, code) as session:
        session.wait_for(b"READY")
        os.write(session.master, b"\x03")
        report, _, _ = session.finish()
    assert report["code"] == 130


@pytest.mark.parametrize("repeat", [False, True])
def test_interruption_kills_stubborn_editor_and_descendant(tmp_path: Path, repeat: bool) -> None:
    code = """
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, lambda *_: os.write(1, b'TERMINATING\\n'))
child_code = '''import fcntl, signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open('descendant.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('DESCENDANT READY', flush=True)
    while True: signal.pause()
'''
subprocess.Popen([sys.executable, '-c', child_code])
while True: signal.pause()
"""
    with editor_session(tmp_path, code) as session:
        session.wait_for(b"DESCENDANT READY")
        os.kill(session.process.pid, signal.SIGTERM)
        session.wait_for(b"TERMINATING")
        if repeat:
            os.kill(session.process.pid, signal.SIGINT)
        report, _, _ = session.finish()
        with (tmp_path / "descendant.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads((tmp_path / "editor.json").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(record["pid"], 0)
    assert report["code"] == 143


@pytest.mark.parametrize("code", [0, 9])
def test_editor_exits_before_foreground_handoff(tmp_path: Path, code: int) -> None:
    with editor_session(tmp_path, f"sys.exit({code})", {"immediate": True}) as session:
        report, _, _ = session.finish()
    assert report["code"] == (2 if code else 0)
    if code:
        assert "status 9" in report["error"]
    else:
        assert report["value"] == "draft"


def test_edited_prompt_accepts_exact_byte_limit(tmp_path: Path) -> None:
    with editor_session(tmp_path, "path.write_bytes(b'x' * 1048576)") as session:
        report, _, _ = session.finish()
    assert report["code"] == 0
    assert report["value"] == "x" * 1048576


def test_edit_cli_composes_draft_and_keeps_pipelines_separate(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("""import json, os, sys
prompt = sys.stdin.buffer.read().decode()
answer = json.dumps(dict(prompt=prompt, cwd=os.getcwd()))
print(json.dumps(dict(type='thread.started', thread_id='fixture')))
print(json.dumps(dict(type='turn.started')))
print(json.dumps(dict(type='item.completed', item=dict(id='answer', type='agent_message', text=answer))))
print(json.dumps(dict(type='turn.completed', usage=dict(input_tokens=1, cached_input_tokens=0, output_tokens=2))))
""")
    (tmp_path / "project").mkdir()
    (tmp_path / "context.md").write_text("$input context\n")
    (tmp_path / ".pratfile").write_text(
        "version=1\n[agents.codex]\ncommand="
        + json.dumps([sys.executable, str(agent)])
        + '\n[templates.review]\nprompt="Review $input"\n'
    )
    code = """
os.write(1, b'EDITING\\n')
addition = os.read(0, 100)
path.write_bytes(path.read_bytes() + addition)
"""
    request: dict[str, object] = {
        "args": [
            "-e",
            "cx",
            "task",
            "-t",
            "review",
            "--context",
            "context.md",
            "--cwd",
            "project",
            "--json",
            "--timeout",
            "0.1",
        ]
    }
    with editor_session(tmp_path, code, request) as session:
        assert session.process.stdin is not None
        session.process.stdin.write(b"pipeline input")
        session.process.stdin.close()
        session.process.stdin = None
        session.wait_for(b"EDITING")
        time.sleep(0.15)
        os.write(session.master, b" + edited\n")
        report, stdout, stderr = session.finish()
    draft = '# Context: "context.md"\n\n$input context\n\n\nReview pipeline input\n\ntask'
    editor = json.loads((tmp_path / "editor.json").read_text())
    assert editor["draft"] == draft
    assert editor["cwd"] == str(tmp_path)
    assert report["code"] == 0, stderr
    result = json.loads(stdout)
    assert result["status"] == "success"
    assert json.loads(result["output"]) == {
        "prompt": draft + " + edited\n",
        "cwd": str(tmp_path / "project"),
    }


@pytest.mark.parametrize(
    "arguments", [[], ["--prompt="], ["--prompt= \n"], ["--template", "blank"]]
)
def test_edit_cli_allows_absent_or_blank_draft(tmp_path: Path, arguments: list[str]) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("import json; print(json.dumps({'response': 'answer'}))")
    (tmp_path / ".pratfile").write_text(
        "version=1\n[agents.gemini]\ncommand="
        + json.dumps([sys.executable, str(agent)])
        + '\n[templates.blank]\nprompt="$input"\n'
    )
    with editor_session(
        tmp_path,
        "path.write_text('final prompt')",
        {"args": ["gm", "--edit", "--json", *arguments]},
    ) as session:
        assert session.process.stdin is not None
        session.process.stdin.close()
        session.process.stdin = None
        report, stdout, _ = session.finish()
    assert report["code"] == 0
    assert json.loads(stdout)["output"] == "answer"
    assert json.loads((tmp_path / "editor.json").read_text())["draft"] == (
        " \n" if arguments == ["--prompt= \n"] else ""
    )


def test_editor_resumes_sigttin_stop_after_handoff(tmp_path: Path) -> None:
    code = "os.kill(os.getpid(), signal.SIGTTIN)\npath.write_text('resumed')"
    with editor_session(tmp_path, code) as session:
        report, _, _ = session.finish()
    assert report["value"] == "resumed"


@pytest.mark.parametrize("status", [0, 7])
def test_editor_exit_cleans_up_descendants(tmp_path: Path, status: int) -> None:
    code = (
        """
child_code = '''import fcntl, signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open('descendant.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('ready', flush=True)
    while True: signal.pause()
'''
child = subprocess.Popen([sys.executable, '-c', child_code], stdout=subprocess.PIPE)
assert child.stdout.readline() == b'ready\\n'
"""
        + f"sys.exit({status})"
    )
    with editor_session(tmp_path, code) as session:
        report, _, _ = session.finish()
        with (tmp_path / "descendant.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert report["code"] == (2 if status else 0)
    if status:
        assert "status 7" in report["error"]
    else:
        assert report["value"] == "draft"
