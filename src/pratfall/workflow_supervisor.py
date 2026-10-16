import errno
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import IO, NamedTuple, cast

from pratfall.codes import (
    EXECUTABLE_NOT_EXECUTABLE,
    EXECUTABLE_NOT_FOUND,
    INTERNAL_ERROR,
    INTERRUPTED,
    INVALID_ARGUMENTS,
    PROCESS_IO_ERROR,
    SIGNAL_EXIT_BASE,
    TIMEOUT,
)
from pratfall.errors import PratError
from pratfall.interruption import Handler, handling
from pratfall.limits import TERMINATE_GRACE, WORKFLOW_RESULT_BYTES
from pratfall.models import Workflow, WorkflowStep
from pratfall.nonblocking import write_nonblocking
from pratfall.workflow_plan import (
    EachItems,
    StepOutcome,
    StepResult,
    each_items,
    each_result,
    input_problem,
    instance_arguments,
    instance_input,
    item_labels,
    ready_work,
    step_arguments,
    step_input,
    step_result,
    synthesized_result,
)

WORKFLOW_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
BACKSTOP_SLACK = 10.0
RELAY_LINE_BYTES = 64 * 1024
_TICK = 0.05
_READ_SIZE = 64 * 1024


@dataclass(frozen=True)
class PreparedWorkflow:
    name: str
    workflow: Workflow
    prompts: Mapping[str, str | None]
    values: Mapping[str, str]
    timeouts: Mapping[str, float]
    base: str | None
    passthrough: tuple[str, ...]


class Supervised(NamedTuple):
    outcomes: tuple[StepOutcome, ...]
    interrupted_by: int | None
    items: Mapping[str, EachItems]


type _Work = tuple[str, int | None]


@dataclass(eq=False)
class _Child:
    step: WorkflowStep
    item: int | None
    order: tuple[int, int]
    prefix: bytes
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
            _write_available(sys.stderr.fileno(), data)
        except (AttributeError, OSError, ValueError):
            self.failed = True


@dataclass
class _Supervision:
    prepared: PreparedWorkflow
    launcher: tuple[str, ...]
    cwd: Path
    selector: selectors.BaseSelector
    signals: list[int] = field(default_factory=list)
    outcomes: list[StepOutcome] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    items: dict[str, tuple[str, ...]] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    running: dict[_Work, _Child] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)
    launched: Counter[str] = field(default_factory=Counter)
    active: Counter[str] = field(default_factory=Counter)
    instances: dict[str, list[StepOutcome]] = field(default_factory=dict)
    failed: bool = False
    relay: _Relay = field(default_factory=_Relay)


def supervise(prepared: PreparedWorkflow, launcher: tuple[str, ...], cwd: Path) -> Supervised:
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
    return Supervised(tuple(state.outcomes), _first_signal(state), _listed(state))


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


def _stopped(state: _Supervision) -> bool:
    return bool(state.signals) or state.failed


def _launch_ready(state: _Supervision) -> None:
    workflow = state.prepared.workflow
    while not _stopped(state):
        succeeded = state.outputs.keys()
        ready = ready_work(
            workflow, state.items, succeeded, state.started, state.launched, state.active
        )
        if not ready:
            return
        for step, item in ready:
            if _stopped(state):
                return
            _start(state, step, item)


def _start(state: _Supervision, step: WorkflowStep, item: int | None) -> None:
    if item is not None:
        state.launched[step.name] += 1
    else:
        state.started.add(step.name)
        if step.each is not None:
            _prepare_items(state, step, step.each)
            return
    prepared = state.prepared
    prompt = prepared.prompts[step.selector]
    if item is None:
        text = step_input(step, state.outputs, prepared.base)
        problem = input_problem(step, prompt, text, state.outputs)
        arguments = step_arguments(
            step, prompt, prepared.values, state.outputs, prepared.passthrough
        )
        prefix = f"[{step.name}] "
    else:
        value = state.items[step.name][item]
        text = instance_input(step, value, state.outputs, prepared.base)
        problem = input_problem(step, prompt, text, state.outputs, value)
        arguments = instance_arguments(
            step, prompt, value, prepared.values, state.outputs, prepared.passthrough
        )
        prefix = f"[{step.name}[{state.labels[step.name][item]}]] "
    if problem is not None:
        _record(state, step, item, synthesized_result("error", INVALID_ARGUMENTS, problem))
        return
    spec = {"version": 1, "parent": os.getpid(), "arguments": list(arguments), "input": text}
    try:
        process = _spawn(state.launcher, spec, state.cwd)
    except OSError as error:
        _record(state, step, item, _spawn_failure(error, state.launcher[0]))
        return
    deadline = time.monotonic() + prepared.timeouts[step.name] + 2 * TERMINATE_GRACE
    order = (prepared.workflow.steps.index(step), -1 if item is None else item)
    child = _Child(step, item, order, prefix.encode(), process, deadline + BACKSTOP_SLACK)
    state.running[step.name, item] = child
    state.active[step.name] += 1
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            state.selector.register(stream, selectors.EVENT_READ, child)
    _forward(state, child)


def _listed(state: _Supervision) -> dict[str, EachItems]:
    return {name: EachItems(items, state.dropped[name]) for name, items in state.items.items()}


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


def _spawn_failure(error: OSError, executable: str) -> StepResult:
    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        return synthesized_result(
            "error", EXECUTABLE_NOT_FOUND, f"Executable {executable!r} was not found."
        )
    if isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.ENOEXEC}:
        message = f"Executable {executable!r} cannot be executed."
        return synthesized_result("error", EXECUTABLE_NOT_EXECUTABLE, message)
    message = f"Cannot start workflow step {executable!r}: {error}."
    return synthesized_result("error", PROCESS_IO_ERROR, message)


def _prepare_items(state: _Supervision, step: WorkflowStep, source: str) -> None:
    result = next(
        outcome.result
        for outcome in state.outcomes
        if outcome.step == source and outcome.item is None
    )
    try:
        listed = each_items(step, result)
    except PratError as error:
        failure = synthesized_result("error", error.code, str(error), error.exit_code)
        _record(state, step, None, failure)
        return
    state.items[step.name] = listed.items
    state.dropped[step.name] = listed.dropped
    state.labels[step.name] = item_labels(listed.items)
    if listed.dropped:
        kept = len(listed.items)
        state.relay.write(
            f"prat: workflow '{state.prepared.name}' step '{step.name}': "
            f"running the first {kept} of {kept + listed.dropped} items.\n".encode()
        )
    if not listed.items:
        state.outputs[step.name] = ""


def _record(state: _Supervision, step: WorkflowStep, item: int | None, result: StepResult) -> None:
    outcome = StepOutcome(step.name, result, item)
    state.outcomes.append(outcome)
    if item is None:
        _publish(state, step.name, result)
        return
    instances = state.instances.setdefault(step.name, [])
    instances.append(outcome)
    items = state.items[step.name]
    if result.status != "success":
        state.failed = True
    elif len(instances) == len(items):
        _publish(
            state,
            step.name,
            each_result(step, items, tuple(instances), True, state.dropped[step.name]),
        )


def _publish(state: _Supervision, name: str, result: StepResult) -> None:
    if result.status == "success":
        state.outputs[name] = result.output
    else:
        state.failed = True


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
    for child in sorted([*expired, *finished], key=lambda child: child.order):
        del state.running[child.step.name, child.item]
        state.active[child.step.name] -= 1
        _record(state, child.step, child.item, _outcome_of(state, child, expired=child in expired))


def _outcome_of(state: _Supervision, child: _Child, *, expired: bool) -> StepResult:
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
        state.relay.write(b"".join(child.prefix + line for line in lines))


def _relay(state: _Supervision, child: _Child, line: bytes) -> None:
    state.relay.write(child.prefix + line)


def _write_available(descriptor: int, data: bytes) -> None:
    offset = write_nonblocking(descriptor, data)
    if 0 < offset < len(data) and data[offset - 1] != ord("\n"):
        with suppress(OSError):
            write_nonblocking(descriptor, b"\n")


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


def _backstop(timeout: float) -> StepResult:
    seconds = timeout + 2 * TERMINATE_GRACE + BACKSTOP_SLACK
    message = f"exceeded the workflow backstop of {seconds:g} seconds."
    return synthesized_result("timeout", TIMEOUT, message)


def _child_result(
    overflow: bool, stdout: bytes, returncode: int, interrupting: int | None
) -> StepResult:
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
    if result.exit_code != returncode:
        message = f"exited with status {returncode} but reported exit code {result.exit_code}."
        return synthesized_result("error", INTERNAL_ERROR, message)
    return result


def _parse_result(raw: bytes) -> StepResult:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except RecursionError as error:
        raise ValueError("result nesting is too deep") from error
    problem = _shape_problem(value)
    if problem is not None:
        raise ValueError(problem)
    return step_result(value)


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
