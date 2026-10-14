import errno
import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import IO, cast

from pratfall.cli.parsing import _WORKFLOW_FLAGS, RunArguments, _run_cwd, _scan_run
from pratfall.cli.presentation import (
    _emit_result,
    _emit_stdout,
    _guard,
    _internal_message,
    _stderr_is_tty,
    _StdoutLatch,
    _write_nonblocking,
)
from pratfall.codes import (
    EXECUTABLE_NOT_EXECUTABLE,
    EXECUTABLE_NOT_FOUND,
    INTERNAL_ERROR,
    INTERRUPTED,
    INVALID_ARGUMENTS,
    PROCESS_IO_ERROR,
    SIGNAL_EXIT_BASE,
    TIMEOUT,
    exit_code_for,
)
from pratfall.config import merge_options, resolve_profile
from pratfall.errors import PratError
from pratfall.interruption import Handler, handling
from pratfall.limits import TERMINATE_GRACE, WORKFLOW_RESULT_BYTES
from pratfall.models import Config, Workflow, WorkflowStep
from pratfall.notify import encode, notification_for, send, should_notify
from pratfall.prompt_input import PROMPT_LIMIT, InputInterrupted, PromptSource, acquire_prompt
from pratfall.prompt_templates import bind_input
from pratfall.workflow_plan import (
    StepOutcome,
    early_result,
    ready_steps,
    step_arguments,
    step_input,
    step_parameters,
    synthesized_result,
    trimmed_output,
    workflow_parameters,
    workflow_prompts,
    workflow_result,
)

LAUNCHER: tuple[str, ...] = (sys.executable, "-P", "-m", "pratfall", "__step")
WORKFLOW_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
BACKSTOP_SLACK = 10.0
RELAY_LINE_BYTES = 64 * 1024
_TICK = 0.05
_READ_SIZE = 64 * 1024


@dataclass(frozen=True)
class _Prepared:
    name: str
    workflow: Workflow
    prompts: Mapping[str, str | None]
    values: Mapping[str, str]
    timeouts: Mapping[str, float]
    base: str | None
    passthrough: tuple[str, ...]


@dataclass(eq=False)
class _Child:
    step: WorkflowStep
    index: int
    process: subprocess.Popen[bytes]
    deadline: float
    stdout: bytearray = field(default_factory=bytearray)
    line: bytearray = field(default_factory=bytearray)
    overflow: bool = False
    open_streams: int = 2
    forwarded: int = 0
    signalled: bool = False


@dataclass
class _Relay:
    failed: bool = False

    def write(self, data: bytes) -> None:
        if self.failed:
            return
        try:
            descriptor = sys.stderr.fileno()
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        except (AttributeError, OSError, ValueError):
            self.failed = True
            return
        try:
            fcntl.fcntl(descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            _write_available(descriptor, data)
        except OSError:
            self.failed = True
        finally:
            with suppress(OSError):
                fcntl.fcntl(descriptor, fcntl.F_SETFL, flags)


@dataclass
class _Supervision:
    prepared: _Prepared
    launcher: tuple[str, ...]
    cwd: Path
    selector: selectors.BaseSelector
    signals: list[int] = field(default_factory=list)
    outcomes: list[StepOutcome] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    running: dict[str, _Child] = field(default_factory=dict)
    relay: _Relay = field(default_factory=_Relay)


def run_workflow(
    arguments: list[str],
    parsed: RunArguments,
    config: Config,
    invocation_cwd: Path,
    latch: _StdoutLatch,
    *,
    launcher: tuple[str, ...] = LAUNCHER,
) -> int:
    return _guard(
        lambda: _run_workflow(arguments, parsed, config, invocation_cwd, latch, launcher),
        lambda error: _internal_failure(parsed, latch, error),
    )


def _run_workflow(
    arguments: list[str],
    parsed: RunArguments,
    config: Config,
    invocation_cwd: Path,
    latch: _StdoutLatch,
    launcher: tuple[str, ...],
) -> int:
    name = parsed.selector
    try:
        prepared = _prepare(arguments, parsed, config, invocation_cwd)
    except InputInterrupted as error:
        exit_code = SIGNAL_EXIT_BASE + error.signum
        payload = early_result(name, "interrupted", INTERRUPTED, exit_code, str(error))
        return _emit_early(payload, latch, json_mode=parsed.json)
    except PratError as error:
        payload = early_result(name, "error", error.code, error.exit_code, str(error))
        return _emit_early(payload, latch, json_mode=parsed.json)
    if parsed.dry_run:
        _emit_dry_run(prepared, latch, json_mode=parsed.json)
        return 0
    started = time.monotonic()
    outcomes, interrupted_by = _supervise(prepared, launcher, invocation_cwd)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    payload = workflow_result(name, prepared.workflow, outcomes, duration_ms, interrupted_by)
    _emit_workflow(payload, latch, json_mode=parsed.json)
    _guard(lambda: _notify(config, parsed, payload), lambda _: None)
    return cast(int, payload["exit_code"])


def _prepare(
    arguments: list[str], parsed: RunArguments, config: Config, invocation_cwd: Path
) -> _Prepared:
    name = parsed.selector
    workflow = config.workflows[name]
    _reject_flags(arguments)
    prompts = workflow_prompts(config, workflow)
    names = workflow_parameters(workflow, prompts)
    label = f"workflow '{name}'"
    bound = bind_input(
        names, label, parsed.words, parsed.parameters, parsed.prompt_source, active=True
    )
    consumes = any(not step.after and step.input == "upstream" for step in workflow.steps)
    if not consumes and bound.source is not None:
        raise PratError(f"{label} takes no input.", code=INVALID_ARGUMENTS)
    timeouts = {step.name: _step_timeout(config, name, step, parsed) for step in workflow.steps}
    _run_cwd(parsed.cwd, invocation_cwd)
    base = _base_input(bound.source, invocation_cwd) if consumes else None
    return _Prepared(
        name, workflow, prompts, bound.parameters, timeouts, base, _passthrough(parsed)
    )


def _reject_flags(arguments: list[str]) -> None:
    scan = _scan_run(arguments)
    flags = (
        token.name
        for token in scan.tokens
        if token.argument.startswith("-") and token.argument != "-"
    )
    rejected = next((flag for flag in flags if flag not in _WORKFLOW_FLAGS), None)
    if rejected is None and scan.native_arguments is not None:
        rejected = "--"
    if rejected is not None:
        raise PratError(f"{rejected} is not supported for workflows.", code=INVALID_ARGUMENTS)


def _step_timeout(config: Config, name: str, step: WorkflowStep, parsed: RunArguments) -> float:
    try:
        timeout = resolve_profile(config, step.selector, parsed.options).options.timeout
    except PratError as error:
        raise PratError(
            f"workflow '{name}' step '{step.name}': {error}", code=error.code
        ) from error
    if timeout is None:
        raise PratError("Resolved timeout is missing.", code=INVALID_ARGUMENTS)
    return timeout


def _base_input(source: PromptSource | None, invocation_cwd: Path) -> str | None:
    return acquire_prompt(source, invocation_cwd, allow_absent=True).decode("utf-8") or None


def _passthrough(parsed: RunArguments) -> tuple[str, ...]:
    timeout = parsed.options.timeout
    values = (
        ("--config", parsed.config),
        ("--cwd", parsed.cwd),
        ("--timeout", None if timeout is None else repr(timeout)),
    )
    switches = (("--progress", parsed.progress), ("--trace", parsed.trace))
    return (
        *(f"{flag}={value}" for flag, value in values if value is not None),
        *(flag for flag, enabled in switches if enabled),
    )


def _emit_early(payload: dict[str, object], latch: _StdoutLatch, *, json_mode: bool) -> int:
    if json_mode:
        _emit_stdout(json.dumps(payload, ensure_ascii=False), latch)
    else:
        print(f"prat: {cast(dict[str, str], payload['error'])['message']}", file=sys.stderr)
    return cast(int, payload["exit_code"])


def _internal_failure(parsed: RunArguments, latch: _StdoutLatch, error: Exception) -> int:
    message = _internal_message(error)
    exit_code = exit_code_for(INTERNAL_ERROR)
    payload = early_result(parsed.selector, "error", INTERNAL_ERROR, exit_code, message)
    with suppress(OSError, ValueError):
        if parsed.json and not latch.emitted:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"prat: {message}", file=sys.stderr)
    return exit_code


def _step_parameters(prepared: _Prepared, step: WorkflowStep) -> dict[str, str]:
    return step_parameters(step, prepared.prompts[step.selector], prepared.values)


def _emit_dry_run(prepared: _Prepared, latch: _StdoutLatch, *, json_mode: bool) -> None:
    if json_mode:
        _emit_stdout(json.dumps(_dry_run_payload(prepared), ensure_ascii=False), latch)
        return
    lines = (_dry_run_line(prepared, step) for step in prepared.workflow.steps)
    _emit_stdout("\n".join(lines), latch)


def _dry_run_payload(prepared: _Prepared) -> dict[str, object]:
    return {
        "schema_version": 1,
        "workflow": prepared.name,
        "dry_run": True,
        "parameters": dict(prepared.values),
        "steps": [
            {
                "step": step.name,
                "profile": step.selector,
                "after": list(step.after),
                "input": step.input,
                "bind": dict(step.bind),
                "parameters": _step_parameters(prepared, step),
            }
            for step in prepared.workflow.steps
        ],
    }


def _dry_run_line(prepared: _Prepared, step: WorkflowStep) -> str:
    line = (
        f"{step.name}: profile={step.selector} after={','.join(step.after) or 'none'} "
        f"input={step.input}"
    )
    if step.bind:
        line += " bind=" + ",".join(f"{key}:{source}" for key, source in step.bind.items())
    parameters = _step_parameters(prepared, step)
    if parameters:
        line += " parameters=" + ",".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in parameters.items()
        )
    return line


def _emit_workflow(payload: dict[str, object], latch: _StdoutLatch, *, json_mode: bool) -> None:
    _emit_result(payload, latch, json_mode=json_mode)
    if json_mode or payload["status"] == "success":
        return
    print(_failure_line(payload), file=sys.stderr)


def _failure_line(payload: Mapping[str, object]) -> str:
    name = payload["workflow"]
    message = cast(Mapping[str, str], payload["error"])["message"]
    if payload["status"] == "interrupted":
        return f"prat: workflow '{name}': {message}"
    return f"prat: workflow '{name}' failed at {message}"


def _notify(config: Config, parsed: RunArguments, payload: Mapping[str, object]) -> None:
    options = merge_options(config.defaults, parsed.options)
    status = cast(str, payload["status"])
    duration_ms = cast(int, payload["duration_ms"])
    if not should_notify(options, status, duration_ms, stderr_is_tty=_stderr_is_tty()):
        return
    notification = notification_for(f"workflow {parsed.selector}", status, duration_ms)
    send(encode(notification, options.notify_method or "auto", os.environ))


def _supervise(
    prepared: _Prepared, launcher: tuple[str, ...], cwd: Path
) -> tuple[tuple[StepOutcome, ...], int | None]:
    with selectors.DefaultSelector() as selector:
        state = _Supervision(prepared, launcher, cwd, selector)
        handled = _handled_signals(signal.getsignal(signal.SIGHUP))
        with handling(_recorder(state), handled):
            try:
                _launch_ready(state)
                while state.running:
                    _tick(state)
                    _launch_ready(state)
            finally:
                _teardown(state, list(state.running.values()))
    return tuple(state.outcomes), _first_signal(state)


def _handled_signals(hangup: object) -> tuple[signal.Signals, ...]:
    return tuple(
        chosen
        for chosen in WORKFLOW_SIGNALS
        if chosen != signal.SIGHUP or hangup is not signal.SIG_IGN
    )


def _first_signal(state: _Supervision) -> int | None:
    return state.signals[0] if state.signals else None


def _recorder(state: _Supervision) -> Handler:
    def receive(signum: int, _frame: FrameType | None) -> None:
        state.signals.append(signum)

    return receive


def _failed(state: _Supervision) -> bool:
    return any(outcome.result["status"] != "success" for outcome in state.outcomes)


def _launch_ready(state: _Supervision) -> None:
    workflow = state.prepared.workflow
    while not state.signals and not _failed(state):
        started = frozenset(outcome.step for outcome in state.outcomes)
        running = frozenset(state.running)
        ready = ready_steps(workflow, frozenset(state.outputs), started | running, running)
        if not ready:
            return
        _start(state, ready[0])


def _start(state: _Supervision, step: WorkflowStep) -> None:
    prepared = state.prepared
    prompt = prepared.prompts[step.selector]
    text = step_input(step, state.outputs, prepared.base)
    problem = _input_problem(step, prompt, text, state.outputs)
    if problem is not None:
        _record(state, step.name, synthesized_result("error", INVALID_ARGUMENTS, problem))
        return
    arguments = step_arguments(step, prompt, prepared.values, state.outputs, prepared.passthrough)
    spec = {"version": 1, "parent": os.getpid(), "arguments": list(arguments), "input": text}
    try:
        process = _spawn(state.launcher, spec, state.cwd)
    except OSError as error:
        _record(state, step.name, _spawn_failure(error, state.launcher[0]))
        return
    deadline = time.monotonic() + prepared.timeouts[step.name] + 2 * TERMINATE_GRACE
    index = prepared.workflow.steps.index(step)
    child = _Child(step, index, process, deadline + BACKSTOP_SLACK)
    state.running[step.name] = child
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            state.selector.register(stream, selectors.EVENT_READ, child)
    _forward(state, child)


def _input_problem(
    step: WorkflowStep, prompt: str | None, text: str | None, outputs: Mapping[str, str]
) -> str | None:
    if prompt is None and text is None:
        return "has no prompt and received no input."
    empty = next((name for name, source in step.bind.items() if not outputs[source].strip()), None)
    if empty is not None:
        return f"binds parameter '{empty}' to the empty output of step '{step.bind[empty]}'."
    values = (text or "", *(trimmed_output(outputs[source]) for source in step.bind.values()))
    if sum(len(value.encode("utf-8")) for value in values) > PROMPT_LIMIT:
        return f"input and bound parameters exceed the {PROMPT_LIMIT} byte limit."
    return None


def _spawn(
    launcher: tuple[str, ...], spec: Mapping[str, object], cwd: Path
) -> subprocess.Popen[bytes]:
    with tempfile.TemporaryFile() as spec_file:
        spec_file.write(json.dumps(spec, ensure_ascii=False).encode("utf-8"))
        spec_file.seek(0)
        return subprocess.Popen(
            launcher,
            cwd=cwd,
            stdin=spec_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )


def _spawn_failure(error: OSError, executable: str) -> dict[str, object]:
    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        return synthesized_result(
            "error", EXECUTABLE_NOT_FOUND, f"Executable {executable!r} was not found."
        )
    if isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.ENOEXEC}:
        message = f"Executable {executable!r} cannot be executed."
        return synthesized_result("error", EXECUTABLE_NOT_EXECUTABLE, message)
    message = f"Cannot start workflow step {executable!r}: {error}."
    return synthesized_result("error", PROCESS_IO_ERROR, message)


def _record(state: _Supervision, step: str, result: Mapping[str, object]) -> None:
    state.outcomes.append(StepOutcome(step, result))
    if result["status"] == "success":
        state.outputs[step] = cast(str, result["output"])


def _tick(state: _Supervision) -> None:
    for key, _ in state.selector.select(_TICK):
        _read(state, key)
    for child in state.running.values():
        _forward(state, child)
    now = time.monotonic()
    expired = [child for child in state.running.values() if now >= child.deadline]
    _teardown(state, expired)
    finished = [
        child
        for child in state.running.values()
        if child not in expired and not child.open_streams and child.process.poll() is not None
    ]
    for child in sorted([*expired, *finished], key=lambda child: child.index):
        del state.running[child.step.name]
        _record(state, child.step.name, _outcome_of(state, child, expired=child in expired))


def _outcome_of(state: _Supervision, child: _Child, *, expired: bool) -> Mapping[str, object]:
    if expired:
        return _backstop(state.prepared.timeouts[child.step.name])
    interrupting = _first_signal(state) if child.signalled else None
    stdout, returncode = bytes(child.stdout), child.process.returncode
    return _child_result(child.overflow, stdout, returncode, interrupting)


def _read(state: _Supervision, key: selectors.SelectorKey) -> None:
    child = cast(_Child, key.data)
    is_stdout = key.fileobj is child.process.stdout
    try:
        chunk = os.read(key.fd, _READ_SIZE)
    except BlockingIOError:
        return
    except OSError:
        chunk = b""
    if not chunk:
        _close_stream(state, child, cast(IO[bytes], key.fileobj))
        if not is_stdout and child.line:
            _relay(state, child, bytes(child.line) + b"\n")
            child.line.clear()
        return
    if is_stdout:
        _collect(child, chunk)
        return
    child.line.extend(chunk)
    _relay_lines(state, child)


def _collect(child: _Child, chunk: bytes) -> None:
    if child.overflow or len(child.stdout) + len(chunk) > WORKFLOW_RESULT_BYTES:
        child.overflow = True
        child.stdout.clear()
        return
    child.stdout.extend(chunk)


def _relay_lines(state: _Supervision, child: _Child) -> None:
    lines: list[bytes] = []
    while child.line:
        end = child.line.find(b"\n", 0, RELAY_LINE_BYTES)
        if end >= 0:
            lines.append(bytes(child.line[: end + 1]))
            del child.line[: end + 1]
        elif len(child.line) >= RELAY_LINE_BYTES:
            lines.append(bytes(child.line[:RELAY_LINE_BYTES]) + b"\n")
            del child.line[:RELAY_LINE_BYTES]
        else:
            break
    if lines:
        prefix = f"[{child.step.name}] ".encode()
        state.relay.write(b"".join(prefix + line for line in lines))


def _relay(state: _Supervision, child: _Child, line: bytes) -> None:
    state.relay.write(f"[{child.step.name}] ".encode() + line)


def _write_available(descriptor: int, data: bytes) -> None:
    offset = _write_nonblocking(descriptor, data)
    if 0 < offset < len(data) and data[offset - 1] != ord("\n"):
        with suppress(OSError):
            os.write(descriptor, b"\n")


def _close_stream(state: _Supervision, child: _Child, stream: IO[bytes]) -> None:
    with suppress(KeyError, ValueError):
        state.selector.unregister(stream)
    stream.close()
    child.open_streams -= 1


def _forward(state: _Supervision, child: _Child) -> None:
    count = len(state.signals)
    for signum in state.signals[child.forwarded : count]:
        chosen = signal.SIGTERM if signum == signal.SIGHUP else signal.Signals(signum)
        if _signal_child(child.process, chosen):
            child.signalled = True
    child.forwarded = count


def _signal_child(process: subprocess.Popen[bytes], chosen: signal.Signals) -> bool:
    if process.poll() is not None:
        return False
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, chosen)
        return True
    return False


def _teardown(state: _Supervision, children: list[_Child]) -> None:
    if not children:
        return
    for child in children:
        for stream in (child.process.stdout, child.process.stderr):
            if stream is not None and not stream.closed:
                _close_stream(state, child, stream)
    processes = [child.process for child in children]
    for _ in range(2):
        live = [process for process in processes if process.poll() is None]
        for process in live:
            _signal_child(process, signal.SIGTERM)
        _wait_all(live, TERMINATE_GRACE)
    for process in processes:
        _signal_child(process, signal.SIGKILL)
        process.wait()


def _wait_all(processes: list[subprocess.Popen[bytes]], grace: float) -> None:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(process.poll() is None for process in processes):
        time.sleep(_TICK)


def _backstop(timeout: float) -> dict[str, object]:
    seconds = timeout + 2 * TERMINATE_GRACE + BACKSTOP_SLACK
    message = f"exceeded the workflow backstop of {seconds:g} seconds."
    return synthesized_result("timeout", TIMEOUT, message)


def _child_result(
    overflow: bool, stdout: bytes, returncode: int, interrupting: int | None
) -> Mapping[str, object]:
    if overflow:
        message = f"result exceeded {WORKFLOW_RESULT_BYTES} bytes."
        return synthesized_result("error", INTERNAL_ERROR, message)
    try:
        result = _parse_result(stdout)
    except ValueError as error:
        if interrupting is not None:
            message = f"Interrupted by signal {interrupting}."
            exit_code = SIGNAL_EXIT_BASE + interrupting
            return synthesized_result("interrupted", INTERRUPTED, message, exit_code)
        message = f"exited with status {returncode} without a valid result: {error}"
        return synthesized_result("error", INTERNAL_ERROR, message)
    if result["exit_code"] != returncode:
        message = f"exited with status {returncode} but reported exit code {result['exit_code']}."
        return synthesized_result("error", INTERNAL_ERROR, message)
    return result


def _parse_result(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except RecursionError as error:
        raise ValueError("result nesting is too deep") from error
    problem = _shape_problem(value)
    if problem is not None:
        raise ValueError(problem)
    return cast(dict[str, object], value)


def _shape_problem(value: object) -> str | None:
    if not isinstance(value, dict):
        return "result is not a JSON object"
    invalid = next(
        (key for key, check in _RESULT_FIELDS if key not in value or not check(value[key])), None
    )
    return None if invalid is None else f"field '{invalid}' is missing or has the wrong type"


def _reject_constant(constant: str) -> object:
    raise ValueError(f"nonfinite number {constant}")


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_cost(value: object) -> bool:
    return value is None or type(value) in {int, float}


def _is_usage(value: object) -> bool:
    return value is None or (
        isinstance(value, dict)
        and all(item is None or type(item) is int for item in value.values())
    )


def _is_error(value: object) -> bool:
    return value is None or (
        isinstance(value, dict)
        and isinstance(value.get("code"), str)
        and isinstance(value.get("message"), str)
    )


_RESULT_FIELDS = (
    ("status", _is_str),
    ("exit_code", _is_int),
    ("output", _is_str),
    ("usage", _is_usage),
    ("cost_usd", _is_cost),
    ("error", _is_error),
)
