import errno
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from functools import partial
from pathlib import Path

import pytest

from pratfall.cli import dispatch as dispatch_module
from pratfall.cli import main
from pratfall.cli import workflow_run as workflow_module
from pratfall.errors import PratError
from pratfall.prompt_input import InputInterrupted
from test_run_cli import isolate_notify_env, write_agent

FAKE = """import json, os, sys, time
from pathlib import Path

prompt = sys.stdin.buffer.read().decode()
lines = []
for line in prompt.split("\\n"):
    command, _, argument = line.partition(" ")
    if command == "@touch":
        Path(argument).write_text(str(os.getpid()))
    elif command == "@wait":
        deadline = time.monotonic() + 30
        while not Path(argument).exists():
            if time.monotonic() > deadline:
                sys.exit(9)
            time.sleep(0.02)
    elif command == "@hang":
        time.sleep(60)
    elif command == "@exit":
        sys.exit(int(argument))
    elif command == "@stderr":
        print(argument, file=sys.stderr, flush=True)
    elif command == "@log":
        with open(argument, "a") as stream:
            stream.write("called\\n")
    elif command == "@exists":
        lines.append("marker=" + ("present" if Path(argument).exists() else "absent"))
    elif command == "@blank":
        lines.append("  ")
    elif command == "@big":
        lines.append("a" * int(argument))
    else:
        lines.append(line)
answer = "\\n".join(lines)
usage = {"input_tokens": 2, "output_tokens": 3}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "usage": usage}))
"""

LAUNCHER = """import json, os, signal, sys, time

spec = json.loads(sys.stdin.buffer.read().decode())
mode = sys.argv[1]
result = {"schema_version": 1, "status": "success", "exit_code": 0, "output": "ok", "usage": None, "cost_usd": None, "error": None}
if mode == "echo":
    result["output"] = json.dumps(spec)
    print(json.dumps(result))
elif mode == "invalid":
    print("not json")
elif mode == "wrong-type":
    result["exit_code"] = "0"
    print(json.dumps(result))
elif mode == "oversized":
    print(json.dumps(result) + " " * 4096)
elif mode == "mismatch":
    print(json.dumps(result))
    sys.exit(3)
elif mode == "partial":
    sys.stderr.write("partial")
    sys.stderr.flush()
    print(json.dumps(result))
elif mode == "ignore":
    ready, got, release = sys.argv[2:5]
    def note(signum, frame):
        with open(got, "a") as stream:
            stream.write(f"{signum}\\n")
    for chosen in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(chosen, note)
    open(ready, "w").close()
    deadline = time.monotonic() + 30
    while not os.path.exists(release):
        if time.monotonic() > deadline:
            sys.exit(9)
        time.sleep(0.02)
    print(json.dumps(result))
elif mode == "hang":
    if len(sys.argv) > 2:
        open(sys.argv[2], "w").close()
    time.sleep(60)
elif mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(sys.argv[2], "w").close()
    time.sleep(60)
elif mode == "lines":
    sys.stderr.write("one\\ntwo\\n")
    sys.stderr.flush()
    print(json.dumps(result))
"""

RESULT_KEYS = [
    "schema_version",
    "workflow",
    "status",
    "exit_code",
    "output",
    "duration_ms",
    "usage",
    "cost_usd",
    "error",
    "steps",
]


def workflow_config(
    tmp_path: Path, profiles: dict[str, str], workflows: str, *, agent_dir: Path | None = None
) -> Path:
    directory = agent_dir or tmp_path
    directory.mkdir(exist_ok=True)
    config = write_agent(directory, "claude", FAKE)
    sections = "".join(
        f'\n[profiles.{name}]\nagent="claude"\nprompt={json.dumps(prompt)}\n'
        for name, prompt in profiles.items()
    )
    config.write_text(config.read_text() + sections + "\n" + workflows, encoding="utf-8")
    return config


def chain_config(tmp_path: Path, first: str = "first: $input") -> Path:
    return workflow_config(
        tmp_path,
        {"first": first, "second": "second: $input"},
        '[workflows.chain]\nsteps = [{name="a", profile="first"}, {name="b", profile="second"}]\n',
    )


def json_result(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    result: dict[str, object] = json.loads(capsys.readouterr().out)
    return result


def with_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, *extra: str) -> None:
    script = tmp_path / "launcher.py"
    script.write_text(LAUNCHER, encoding="utf-8")
    launcher = (sys.executable, str(script), mode, *extra)
    monkeypatch.setattr(
        dispatch_module, "run_workflow", partial(workflow_module.run_workflow, launcher=launcher)
    )


def failed_step(
    step: str, status: str, exit_code: int, code: str, message: str
) -> dict[str, object]:
    return {
        "step": step,
        "schema_version": 1,
        "status": status,
        "exit_code": exit_code,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": {"code": code, "message": message},
    }


def steps_of(result: dict[str, object]) -> list[dict[str, object]]:
    steps = result["steps"]
    assert isinstance(steps, list)
    return steps


def test_chain_passes_step_output_to_next_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=hello", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "second: first: hello\n"


def test_file_base_input_reaches_root_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    (tmp_path / "prompt.md").write_text("from file\n", encoding="utf-8")
    assert main(["chain", "-f", "prompt.md", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "second: first: from file\n"


def test_fan_out_and_merge_joins_outputs_in_after_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"root": "R: $input", "left": "X: $input", "right": "Y: $input", "merge": "M\n$input"},
        "[workflows.fan]\nsteps = [\n"
        '  {name="r", profile="root"},\n'
        '  {name="x", profile="left"},\n'
        '  {name="y", profile="right", after=["r"]},\n'
        '  {name="m", profile="merge", after=["x", "y"]},\n]\n',
    )
    assert main(["fan", "--prompt=in", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "M\n## x\n\nX: R: in\n\n## y\n\nY: R: in\n"


def test_bind_passes_ancestor_output_as_parameter_and_excludes_it_from_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"root": "R: $input", "left": "X: $input", "merge": "report=$report\n$input"},
        "[workflows.bound]\nsteps = [\n"
        '  {name="r", profile="root"},\n'
        '  {name="x", profile="left"},\n'
        '  {name="m", profile="merge", after=["r", "x"], bind={report="r"}},\n]\n',
    )
    assert main(["bound", "--prompt=in", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "report=R: in\nX: R: in\n"


@pytest.mark.parametrize(
    "arguments",
    [["shared", "src"], ["shared", "--param", "folder=src"], ["--param=folder=src", "shared"]],
)
def test_shared_parameter_binds_once_for_every_step(
    arguments: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"first": "A $folder", "second": "B $folder: $input"},
        '[workflows.shared]\nsteps = [{name="a", profile="first"}, {name="b", profile="second"}]\n',
    )
    assert main([*arguments, "--config", str(config)]) == 0
    assert capsys.readouterr().out == "B src: A src\n"


def test_missing_parameter_fails_before_any_step_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "calls.log"
    config = workflow_config(
        tmp_path,
        {"first": f"@log {log}\nA $folder"},
        '[workflows.solo]\nsteps = [{name="a", profile="first"}]\n',
    )
    assert main(["solo", "--json", "--config", str(config)]) == 2
    assert json_result(capsys) == {
        "schema_version": 1,
        "workflow": "solo",
        "status": "error",
        "exit_code": 2,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": {
            "code": "invalid_arguments",
            "message": "workflow 'solo': missing value for parameter(s) folder.",
        },
        "steps": [],
    }
    assert not log.exists()


@pytest.mark.parametrize(
    ("extra", "flag"),
    [
        (["--model", "m"], "--model"),
        (["--model=m"], "--model"),
        (["-t", "tpl"], "-t"),
        (["-x"], "-x"),
        (["--fast"], "--fast"),
        (["--", "--native"], "--"),
    ],
)
def test_unsupported_flags_are_rejected_in_the_users_spelling(
    extra: list[str], flag: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=x", "--config", str(config), *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"prat: {flag} is not supported for workflows.\n"


def test_base_input_for_workflow_without_input_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"first": "A", "second": "B: $input"},
        "[workflows.closed]\nsteps = [\n"
        '  {name="a", profile="first", input="none"},\n'
        '  {name="b", profile="second"},\n]\n',
    )
    assert main(["closed", "--prompt=x", "--json", "--config", str(config)]) == 2
    result = json_result(capsys)
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "workflow 'closed' takes no input.",
    }
    assert result["steps"] == []


def test_unknown_step_selector_is_reported_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    monkeypatch.setattr(workflow_module, "resolve_profile", _unknown_selector)
    assert main(["chain", "--prompt=x", "--config", str(config)]) == 2
    assert capsys.readouterr().err == "prat: workflow 'chain' step 'a': unknown.\n"


def _unknown_selector(*_args: object) -> object:
    raise PratError("unknown.", code="invalid_arguments")


def test_branch_failure_lets_sibling_finish_and_skips_downstream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = tmp_path / "failing"
    config = workflow_config(
        tmp_path,
        {
            "root": "R",
            "bad": f"@touch {failing}\n@exit 3",
            "good": f"@wait {failing}\nY",
            "merge": "M $input",
        },
        "[workflows.fan]\nsteps = [\n"
        '  {name="r", profile="root"},\n'
        '  {name="x", profile="bad"},\n'
        '  {name="y", profile="good", after=["r"]},\n'
        '  {name="m", profile="merge", after=["x", "y"]},\n]\n',
    )
    assert main(["fan", "--json", "--config", str(config)]) == 3
    result = json_result(capsys)
    steps = steps_of(result)
    assert [(step["step"], step["status"]) for step in steps] == [
        ("r", "success"),
        ("x", "error"),
        ("y", "success"),
        ("m", "skipped"),
    ]
    assert steps[3] == {"step": "m", "status": "skipped"}
    assert result["error"] == {
        "code": "native_exit",
        "message": "step 'x': Claude Code exited with status 3.",
    }
    assert result["output"] == ""


def test_text_mode_failure_prints_one_stderr_line_and_no_stdout(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path, first="@exit 4")
    assert main(["chain", "--config", str(config)]) == 4
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err.endswith(
        "prat: workflow 'chain' failed at step 'a': Claude Code exited with status 4.\n"
    )


def test_parallel_branches_run_concurrently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    config = workflow_config(
        tmp_path,
        {"left": f"@touch {left}\n@wait {right}\nX", "right": f"@touch {right}\n@wait {left}\nY"},
        "[workflows.pair]\nsteps = [\n"
        '  {name="x", profile="left"},\n'
        '  {name="y", profile="right", after=[]},\n]\n',
    )
    assert main(["pair", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "## x\n\nX\n\n## y\n\nY\n"


def test_sequential_workflow_runs_one_step_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"left": "X", "right": "Y"},
        "[workflows.pair]\nparallel = false\nsteps = [\n"
        '  {name="x", profile="left"},\n'
        '  {name="y", profile="right", after=[]},\n]\n',
    )
    spawned: list[subprocess.Popen[bytes]] = []
    live_at_spawn: list[int] = []
    real_spawn = workflow_module._spawn

    def spy(
        launcher: tuple[str, ...], spec: Mapping[str, object], cwd: Path
    ) -> subprocess.Popen[bytes]:
        live_at_spawn.append(sum(process.poll() is None for process in spawned))
        process = real_spawn(launcher, spec, cwd)
        spawned.append(process)
        return process

    monkeypatch.setattr(workflow_module, "_spawn", spy)
    assert main(["pair", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "## x\n\nX\n\n## y\n\nY\n"
    assert live_at_spawn == [0, 0]


def test_no_step_starts_after_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "log"
    config = workflow_config(
        tmp_path,
        {"bad": "@exit 3", "other": f"@log {log}"},
        "[workflows.pair]\nparallel = false\nsteps = [\n"
        '  {name="a", profile="bad"},\n'
        '  {name="b", profile="other", after=[]},\n]\n',
    )
    assert main(["pair", "--json", "--config", str(config)]) == 3
    steps = steps_of(json_result(capsys))
    assert [(step["step"], step["status"]) for step in steps] == [("a", "error"), ("b", "skipped")]
    assert not log.exists()


def test_json_result_has_workflow_shape_and_key_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=hi", "--json", "--config", str(config)]) == 0
    result = json_result(capsys)
    assert list(result) == RESULT_KEYS
    assert result["workflow"] == "chain"
    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["output"] == "second: first: hi"
    assert result["error"] is None
    assert result["usage"] == {
        "input_tokens": 4,
        "cached_input_tokens": None,
        "cache_write_input_tokens": None,
        "output_tokens": 6,
        "reasoning_output_tokens": None,
    }
    steps = steps_of(result)
    assert [list(step)[:3] for step in steps] == [["step", "schema_version", "agent"]] * 2
    assert [(step["step"], step["output"]) for step in steps] == [
        ("a", "first: hi"),
        ("b", "second: first: hi"),
    ]


def test_child_stderr_is_relayed_with_step_prefix(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path, first="@stderr native note\nfirst")
    assert main(["chain", "--config", str(config)]) == 0
    captured = capfd.readouterr()
    assert captured.out == "second: first\n"
    assert "[a] native note\n" in captured.err
    assert "[a] prat: launching Claude Code\n" in captured.err
    assert "[b] prat: launching Claude Code\n" in captured.err


def test_long_stderr_lines_are_split_at_the_relay_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(workflow_module, "RELAY_LINE_BYTES", 8)
    config = chain_config(tmp_path, first="@stderr 0123456789abcdefXY\nfirst")
    assert main(["chain", "--config", str(config)]) == 0
    err = capfd.readouterr().err
    assert "[a] 01234567\n[a] 89abcdef\n[a] XY\n" in err


def test_dry_run_text_lists_each_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = dry_run_config(tmp_path)
    assert main(["tidy", "src", "--dry-run", "--config", str(config)]) == 0
    assert capsys.readouterr().out == (
        'clean: profile=remove-empty after=none input=upstream parameters=folder="src"\n'
        "pack: profile=compress after=clean input=upstream\n"
        "check: profile=verify after=clean,pack input=none bind=report:pack\n"
    )


def test_dry_run_json_matches_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = dry_run_config(tmp_path)
    assert (
        main(["tidy", "--param", "folder=src", "--dry-run", "--json", "--config", str(config)]) == 0
    )
    assert json_result(capsys) == {
        "schema_version": 1,
        "workflow": "tidy",
        "dry_run": True,
        "parameters": {"folder": "src"},
        "steps": [
            {
                "step": "clean",
                "profile": "remove-empty",
                "after": [],
                "input": "upstream",
                "bind": {},
                "parameters": {"folder": "src"},
            },
            {
                "step": "pack",
                "profile": "compress",
                "after": ["clean"],
                "input": "upstream",
                "bind": {},
                "parameters": {},
            },
            {
                "step": "check",
                "profile": "verify",
                "after": ["clean", "pack"],
                "input": "none",
                "bind": {"report": "pack"},
                "parameters": {},
            },
        ],
    }


def dry_run_config(tmp_path: Path) -> Path:
    log = tmp_path / "calls.log"
    return workflow_config(
        tmp_path,
        {
            "remove-empty": f"@log {log}\nRemove empty files in $folder",
            "compress": "Compress $input",
            "verify": "Verify $report",
        },
        "[workflows.tidy]\nsteps = [\n"
        '  {name="clean", profile="remove-empty"},\n'
        '  {name="pack", profile="compress"},\n'
        '  {name="check", profile="verify", after=["clean", "pack"], input="none",'
        ' bind={report="pack"}},\n]\n',
    )


def test_children_receive_spec_with_parent_and_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"first": "A $folder: $input"},
        '[workflows.solo]\nsteps = [{name="a", profile="first"}]\n',
    )
    with_launcher(monkeypatch, tmp_path, "echo")
    arguments = ["solo", "ünï", "--prompt=bäse", "--timeout", "5", "--progress", "--trace"]
    assert main([*arguments, "--cwd", ".", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "version": 1,
        "parent": os.getpid(),
        "arguments": [
            "first",
            "--json",
            "--no-notify",
            f"--config={config}",
            "--cwd=.",
            "--timeout=5.0",
            "--progress",
            "--trace",
            "--param",
            "folder=ünï",
        ],
        "input": "bäse",
    }


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (
            "invalid",
            "exited with status 0 without a valid result: Expecting value: line 1 column 1 (char 0)",
        ),
        (
            "wrong-type",
            "exited with status 0 without a valid result: field 'exit_code' is missing or has the wrong type",
        ),
        ("mismatch", "exited with status 3 but reported exit code 0."),
        ("oversized", "result exceeded 1024 bytes."),
    ],
)
def test_bad_child_results_are_internal_errors(
    mode: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(workflow_module, "WORKFLOW_RESULT_BYTES", 1024)
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, mode)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 1
    result = json_result(capsys)
    assert result["error"] == {"code": "internal_error", "message": f"step 'a': {message}"}
    assert steps_of(result) == [
        failed_step("a", "error", 1, "internal_error", message),
        {"step": "b", "status": "skipped"},
    ]


def test_child_without_a_result_reports_its_exit_status() -> None:
    message = (
        "exited with status -9 without a valid result: Expecting value: line 1 column 1 (char 0)"
    )
    expected = failed_step("a", "error", 1, "internal_error", message)
    del expected["step"]
    assert workflow_module._child_result(False, b"", -9, None) == expected


def test_signalled_child_without_a_result_is_interrupted() -> None:
    expected = failed_step("a", "interrupted", 130, "interrupted", "Interrupted by signal 2.")
    del expected["step"]
    assert workflow_module._child_result(False, b"", -2, signal.SIGINT) == expected


def test_launcher_that_cannot_start_fails_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    launcher = ("/nonexistent/prat-launcher",)
    monkeypatch.setattr(
        dispatch_module, "run_workflow", partial(workflow_module.run_workflow, launcher=launcher)
    )
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 127
    result = json_result(capsys)
    assert result["status"] == "error"
    assert steps_of(result)[0]["error"] == {
        "code": "executable_not_found",
        "message": "Executable '/nonexistent/prat-launcher' was not found.",
    }


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            PermissionError(errno.EACCES, "denied"),
            "executable_not_executable",
            "Executable 'launcher' cannot be executed.",
        ),
        (
            OSError(errno.EIO, "io"),
            "process_io_error",
            "Cannot start workflow step 'launcher': [Errno 5] io.",
        ),
    ],
)
def test_spawn_errors_map_to_launch_codes(error: OSError, code: str, message: str) -> None:
    expected = failed_step(
        "x", "error", 126 if code == "executable_not_executable" else 1, code, message
    )
    del expected["step"]
    assert workflow_module._spawn_failure(error, "launcher") == expected


def test_backstop_tears_down_a_hung_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(workflow_module, "BACKSTOP_SLACK", 0.0)
    monkeypatch.setattr(workflow_module, "TERMINATE_GRACE", 0.1)
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "hang")
    exit_code = main(["chain", "--prompt=x", "--timeout", "0.2", "--json", "--config", str(config)])
    assert exit_code == 124
    result = json_result(capsys)
    assert steps_of(result)[0] == failed_step(
        "a", "timeout", 124, "timeout", "exceeded the workflow backstop of 0.4 seconds."
    )


def spy_spawn(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    spawned: list[subprocess.Popen[bytes]] = []
    real_spawn = workflow_module._spawn

    def spy(
        launcher: tuple[str, ...], spec: Mapping[str, object], cwd: Path
    ) -> subprocess.Popen[bytes]:
        process = real_spawn(launcher, spec, cwd)
        spawned.append(process)
        return process

    monkeypatch.setattr(workflow_module, "_spawn", spy)
    return spawned


def fail_tick_once(monkeypatch: pytest.MonkeyPatch, ready: Path) -> None:
    real_tick = workflow_module._tick

    def tick(state: object) -> None:
        if ready.exists():
            raise RuntimeError("kaboom")
        real_tick(state)  # type: ignore[arg-type]

    monkeypatch.setattr(workflow_module, "_tick", tick)


def test_teardown_kills_a_child_that_ignores_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(workflow_module, "TERMINATE_GRACE", 0.1)
    ready = tmp_path / "ready"
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "stubborn", str(ready))
    spawned = spy_spawn(monkeypatch)
    fail_tick_once(monkeypatch, ready)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 1
    assert json_result(capsys)["error"] == {
        "code": "internal_error",
        "message": "Unexpected failure: RuntimeError: kaboom",
    }
    assert [process.returncode for process in spawned] == [-signal.SIGKILL]


def test_runtime_check_rejects_prompt_less_step_without_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path, {}, '[workflows.bare]\nsteps = [{name="a", profile="claude"}]\n'
    )
    assert main(["bare", "--json", "--config", str(config)]) == 2
    result = json_result(capsys)
    assert steps_of(result) == [
        failed_step("a", "error", 2, "invalid_arguments", "has no prompt and received no input.")
    ]


def test_runtime_check_rejects_bind_to_empty_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"quiet": f"@touch {tmp_path / 'ran'}", "use": "Use $report"},
        "[workflows.bound]\nsteps = [\n"
        '  {name="r", profile="quiet"},\n'
        '  {name="m", profile="use", input="none", bind={report="r"}},\n]\n',
    )
    assert main(["bound", "--json", "--config", str(config)]) == 2
    result = json_result(capsys)
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "step 'm': binds parameter 'report' to the empty output of step 'r'.",
    }


def test_runtime_check_rejects_bind_to_whitespace_only_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"blank": "@blank", "use": "Use $report"},
        "[workflows.bound]\nsteps = [\n"
        '  {name="r", profile="blank"},\n'
        '  {name="m", profile="use", input="none", bind={report="r"}},\n]\n',
    )
    assert main(["bound", "--json", "--config", str(config)]) == 2
    result = json_result(capsys)
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "step 'm': binds parameter 'report' to the empty output of step 'r'.",
    }


@pytest.mark.parametrize(("size", "status"), [(1048576, 0), (1048577, 2)])
def test_bound_parameters_at_the_prompt_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], size: int, status: int
) -> None:
    config = workflow_config(
        tmp_path,
        {"big": f"@big {size}", "use": "$report"},
        "[workflows.bound]\nsteps = [\n"
        '  {name="r", profile="big"},\n'
        '  {name="m", profile="use", input="none", bind={report="r"}},\n]\n',
    )
    assert main(["bound", "--json", "--config", str(config)]) == status
    assert json_result(capsys)["exit_code"] == status


def test_runtime_check_rejects_oversized_step_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = workflow_config(
        tmp_path,
        {"big": "@big 600000", "use": "Use $report\n$input"},
        "[workflows.huge]\nsteps = [\n"
        '  {name="r", profile="big"},\n'
        '  {name="x", profile="big", after=[]},\n'
        '  {name="m", profile="use", after=["r", "x"], bind={report="r"}},\n]\n',
    )
    assert main(["huge", "--json", "--config", str(config)]) == 2
    result = json_result(capsys)
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "step 'm': input and bound parameters exceed the 1048576 byte limit.",
    }


def test_project_json_module_does_not_shadow_child_imports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "json.py").write_text("raise SystemExit('shadowed json')\n", encoding="utf-8")
    config = workflow_config(
        tmp_path,
        {"first": "first: $input", "second": "second: $input"},
        '[workflows.chain]\nsteps = [{name="a", profile="first"}, {name="b", profile="second"}]\n',
        agent_dir=tmp_path / "agent",
    )
    assert main(["chain", "--prompt=hello", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "second: first: hello\n"


def test_notification_names_the_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    with config.open("a", encoding="utf-8") as stream:
        stream.write("\n[defaults]\nnotify_after=0\n")
    isolate_notify_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    calls: list[bytes] = []
    monkeypatch.setattr(workflow_module, "send", calls.append)
    assert main(["chain", "--prompt=x", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "second: first: x\n"
    assert len(calls) == 1
    assert re.fullmatch(rb"\x1b\]9;prat: workflow chain done in \d+s\x07", calls[0])


def test_unexpected_failure_is_a_workflow_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object) -> object:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(workflow_module, "_supervise", boom)
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 1
    result = json_result(capsys)
    assert result["workflow"] == "chain"
    assert result["error"] == {
        "code": "internal_error",
        "message": "Unexpected failure: RuntimeError: kaboom",
    }


def test_unexpected_failure_in_text_mode_prints_one_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object) -> object:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(workflow_module, "_supervise", boom)
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=x", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "prat: Unexpected failure: RuntimeError: kaboom\n")


def test_signal_to_a_vanished_child_group_is_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(_group: int, _signum: int) -> None:
        raise ProcessLookupError

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", vanished)
            assert workflow_module._signal_child(process, signal.SIGTERM) is False
    finally:
        process.kill()
        process.wait()


def wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} was not written"
        time.sleep(0.02)


def assert_gone(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        assert time.monotonic() < deadline, f"process {pid} is still alive"
        time.sleep(0.05)


def hanging_chain(tmp_path: Path) -> tuple[Path, Path]:
    ready = tmp_path / "ready"
    config = chain_config(tmp_path, first=f"@touch {ready}\n@hang")
    return config, ready


@pytest.mark.parametrize(("chosen", "exit_code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_signal_interrupts_running_step_and_skips_the_rest(
    chosen: signal.Signals, exit_code: int, tmp_path: Path
) -> None:
    config, ready = hanging_chain(tmp_path)
    parent = subprocess.Popen(
        [sys.executable, "-P", "-m", "pratfall", "chain", "--json", "--config", str(config)],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(ready)
        parent.send_signal(chosen)
        stdout, stderr = parent.communicate(timeout=60)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.communicate()
    assert parent.returncode == exit_code, stderr
    result = json.loads(stdout)
    assert result["status"] == "interrupted"
    assert result["exit_code"] == exit_code
    assert result["error"] == {
        "code": "interrupted",
        "message": f"Interrupted by signal {int(chosen)}.",
    }
    steps = steps_of(result)
    assert (steps[0]["step"], steps[0]["status"]) == ("a", "interrupted")
    assert steps[1] == {"step": "b", "status": "skipped"}
    assert_gone(int(ready.read_text()))


@pytest.fixture
def hangup_after_ready(tmp_path: Path) -> Iterator[Path]:
    ready = tmp_path / "ready"
    sender = threading.Thread(target=_hang_up_when, args=(ready,))
    sender.start()
    try:
        yield ready
    finally:
        sender.join()


def _hang_up_when(ready: Path) -> None:
    deadline = time.monotonic() + 30
    while not ready.exists():
        if time.monotonic() > deadline:
            return
        time.sleep(0.02)
    os.kill(os.getpid(), signal.SIGHUP)


def test_hangup_is_forwarded_as_terminate(
    tmp_path: Path, hangup_after_ready: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path, first=f"@touch {hangup_after_ready}\n@hang")
    assert main(["chain", "--json", "--config", str(config)]) == 129
    result = json_result(capsys)
    assert result["status"] == "interrupted"
    steps = steps_of(result)
    assert steps[0]["status"] == "interrupted"
    assert steps[0]["exit_code"] == 143
    assert steps[1] == {"step": "b", "status": "skipped"}
    assert_gone(int(hangup_after_ready.read_text()))


@pytest.fixture
def hangup_ignored() -> Iterator[None]:
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, previous)


def hang_up_then_release(ready: Path, release: Path) -> None:
    try:
        poll_until(ready.exists)
        os.kill(os.getpid(), signal.SIGHUP)
    finally:
        release.touch()


@pytest.mark.usefixtures("hangup_ignored")
def test_inherited_ignored_hangup_stays_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ready, got, release = tmp_path / "ready", tmp_path / "got", tmp_path / "release"
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "ignore", str(ready), str(got), str(release))
    sender = threading.Thread(target=hang_up_then_release, args=(ready, release))
    sender.start()
    try:
        exit_code = main(["chain", "--prompt=x", "--json", "--config", str(config)])
    finally:
        sender.join()
    assert exit_code == 0
    result = json_result(capsys)
    assert result["status"] == "success"
    assert [step["status"] for step in steps_of(result)] == ["success", "success"]
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN


def test_child_killed_by_forwarded_signal_without_result_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hangup_after_ready: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "hang", str(hangup_after_ready))
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 129
    result = json_result(capsys)
    assert steps_of(result) == [
        failed_step("a", "interrupted", 129, "interrupted", "Interrupted by signal 1."),
        {"step": "b", "status": "skipped"},
    ]


def test_relay_drops_output_under_backpressure(monkeypatch: pytest.MonkeyPatch) -> None:
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    with suppress(BlockingIOError):
        while True:
            os.write(write_end, b"x" * 65536)
    os.set_blocking(write_end, True)
    with os.fdopen(write_end, "w") as stream, os.fdopen(read_end, "rb") as reader:
        monkeypatch.setattr(sys, "stderr", stream)
        relay = workflow_module._Relay()
        relay.write(b"dropped\n")
        assert relay.failed is False
        assert os.get_blocking(write_end) is True
        monkeypatch.undo()
        stream.close()
        assert set(reader.read()) == {ord("x")}


def test_relay_marks_failure_on_broken_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    with os.fdopen(write_end, "w") as stream:
        monkeypatch.setattr(sys, "stderr", stream)
        relay = workflow_module._Relay()
        relay.write(b"lost\n")
        relay.write(b"ignored\n")
        monkeypatch.undo()
    assert relay.failed is True


def poll_until(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def received_at_least(got: Path, count: int) -> bool:
    return got.exists() and len(got.read_text().split()) >= count


def signal_then_release(
    ready: Path, got: Path, release: Path, chosen: list[signal.Signals]
) -> None:
    try:
        poll_until(ready.exists)
        for count, signum in enumerate(chosen, start=1):
            os.kill(os.getpid(), signum)
            poll_until(partial(received_at_least, got, count))
    finally:
        release.touch()


def test_signals_are_forwarded_once_each_and_no_step_starts_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ready, got, release = tmp_path / "ready", tmp_path / "got", tmp_path / "release"
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "ignore", str(ready), str(got), str(release))
    sender = threading.Thread(
        target=signal_then_release, args=(ready, got, release, [signal.SIGHUP, signal.SIGINT])
    )
    sender.start()
    try:
        exit_code = main(["chain", "--prompt=x", "--json", "--config", str(config)])
    finally:
        sender.join()
    assert exit_code == 129
    result = json_result(capsys)
    assert result["status"] == "interrupted"
    assert got.read_text().split() == ["15", "2"]
    steps = steps_of(result)
    assert steps[0]["status"] == "success"
    assert steps[1] == {"step": "b", "status": "skipped"}


def test_text_mode_interruption_prints_one_stderr_line(
    tmp_path: Path, hangup_after_ready: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path, first=f"@touch {hangup_after_ready}\n@hang")
    assert main(["chain", "--config", str(config)]) == 129
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err.endswith("prat: workflow 'chain': Interrupted by signal 1.\n")


def test_input_interrupted_before_launch_reports_workflow_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(*_args: object, **_kwargs: object) -> bytes:
        raise InputInterrupted(signal.SIGINT)

    monkeypatch.setattr(workflow_module, "acquire_prompt", interrupted)
    config = chain_config(tmp_path)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 130
    result = json_result(capsys)
    assert result["workflow"] == "chain"
    assert result["status"] == "interrupted"
    assert result["error"] == {"code": "interrupted", "message": "Interrupted by signal 2."}
    assert result["steps"] == []


def test_relay_writes_the_lines_of_one_read_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    writes: list[bytes] = []

    def record(_relay: object, data: bytes) -> None:
        writes.append(data)

    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "lines")
    monkeypatch.setattr(workflow_module._Relay, "write", record)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 0
    assert json_result(capsys)["status"] == "success"
    assert writes == [b"[a] one\n[a] two\n", b"[b] one\n[b] two\n"]


def test_step_child_dies_quietly_when_interrupted_before_its_runner_starts(
    tmp_path: Path,
) -> None:
    script = (
        "import os, runpy, signal, sys, time\n"
        "import pratfall.cli\n"
        "def main():\n"
        "    os.kill(os.getpid(), signal.SIGINT)\n"
        "    time.sleep(30)\n"
        "    return 0\n"
        "pratfall.cli.main = main\n"
        "sys.argv = ['prat', '__step']\n"
        "runpy.run_module('pratfall', run_name='__main__')\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert (child.returncode, child.stderr) == (-signal.SIGINT, b"")


def test_partial_stderr_line_is_flushed_at_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "partial")
    assert main(["chain", "--prompt=x", "--config", str(config)]) == 0
    assert "[a] partial\n" in capfd.readouterr().err


def test_relay_ends_a_partially_written_line(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []

    def short_write(_descriptor: int, data: bytes) -> int:
        calls.append(data)
        if len(calls) == 1:
            return 3
        if len(calls) == 2:
            raise BlockingIOError
        return len(data)

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", short_write)
        workflow_module._write_available(2, b"abcdef\n")
    assert calls == [b"abcdef\n", b"def\n", b"\n"]


def test_relay_adds_no_newline_when_a_short_write_ends_on_a_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []

    def short_write(_descriptor: int, data: bytes) -> int:
        calls.append(data)
        if len(calls) == 1:
            return 4
        raise BlockingIOError

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", short_write)
        workflow_module._write_available(2, b"abc\ndef\n")
    assert calls == [b"abc\ndef\n", b"def\n"]


def test_failure_during_setup_still_reaps_the_started_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = chain_config(tmp_path)
    with_launcher(monkeypatch, tmp_path, "hang")
    spawned = spy_spawn(monkeypatch)

    def broken(_descriptor: int, _blocking: bool) -> None:
        raise OSError(errno.EBADF, "broken")

    monkeypatch.setattr(os, "set_blocking", broken)
    assert main(["chain", "--prompt=x", "--json", "--config", str(config)]) == 1
    assert json_result(capsys)["error"] == {
        "code": "internal_error",
        "message": "Unexpected failure: OSError: [Errno 9] broken",
    }
    assert [process.returncode is not None for process in spawned] == [True]


def test_teardown_reaps_running_children_when_supervision_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, ready = hanging_chain(tmp_path)
    real_tick = workflow_module._tick

    def tick(state: object) -> None:
        if ready.exists():
            raise RuntimeError("kaboom")
        real_tick(state)  # type: ignore[arg-type]

    monkeypatch.setattr(workflow_module, "_tick", tick)
    assert main(["chain", "--json", "--config", str(config)]) == 1
    assert json_result(capsys)["error"] == {
        "code": "internal_error",
        "message": "Unexpected failure: RuntimeError: kaboom",
    }
    assert_gone(int(ready.read_text()))
