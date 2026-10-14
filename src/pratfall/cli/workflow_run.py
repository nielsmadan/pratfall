import json
import os
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import cast

from pratfall.cli.parsing import _WORKFLOW_FLAGS, RunArguments, _run_cwd, _scan_run
from pratfall.cli.presentation import (
    _emit_result,
    _emit_stdout,
    _guard,
    _internal_message,
    _stderr_is_tty,
    _StdoutLatch,
)
from pratfall.codes import (
    INTERNAL_ERROR,
    INTERRUPTED,
    INVALID_ARGUMENTS,
    SIGNAL_EXIT_BASE,
    exit_code_for,
)
from pratfall.config import merge_options, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Config, WorkflowStep
from pratfall.notify import encode, notification_for, send, should_notify
from pratfall.prompt_input import InputInterrupted, PromptSource, acquire_prompt
from pratfall.prompt_templates import bind_input
from pratfall.workflow_plan import (
    early_result,
    step_parameters,
    workflow_parameters,
    workflow_prompts,
    workflow_result,
)
from pratfall.workflow_supervisor import PreparedWorkflow, supervise

LAUNCHER: tuple[str, ...] = (sys.executable, "-P", "-m", "pratfall", "__step")


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
    supervised = supervise(prepared, launcher, invocation_cwd)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    payload = workflow_result(
        name,
        prepared.workflow,
        supervised.outcomes,
        duration_ms,
        supervised.interrupted_by,
        supervised.items,
    )
    _emit_workflow(payload, latch, json_mode=parsed.json)
    _guard(lambda: _notify(config, parsed, payload), lambda _: None)
    return cast(int, payload["exit_code"])


def _prepare(
    arguments: list[str], parsed: RunArguments, config: Config, invocation_cwd: Path
) -> PreparedWorkflow:
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
    return PreparedWorkflow(
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


def _step_parameters(prepared: PreparedWorkflow, step: WorkflowStep) -> dict[str, str]:
    return step_parameters(step, prepared.prompts[step.selector], prepared.values)


def _emit_dry_run(prepared: PreparedWorkflow, latch: _StdoutLatch, *, json_mode: bool) -> None:
    if json_mode:
        _emit_stdout(json.dumps(_dry_run_payload(prepared), ensure_ascii=False), latch)
        return
    lines = (_dry_run_line(prepared, step) for step in prepared.workflow.steps)
    _emit_stdout("\n".join(lines), latch)


def _dry_run_payload(prepared: PreparedWorkflow) -> dict[str, object]:
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
                "each": step.each,
                "split": None if step.each is None else step.split,
                "as": step.as_parameter,
                "max_parallel": None if step.each is None else step.max_parallel,
                "parameters": _step_parameters(prepared, step),
            }
            for step in prepared.workflow.steps
        ],
    }


def _dry_run_line(prepared: PreparedWorkflow, step: WorkflowStep) -> str:
    line = (
        f"{step.name}: profile={step.selector} after={','.join(step.after) or 'none'} "
        f"input={step.input}"
    )
    if step.bind:
        line += " bind=" + ",".join(f"{key}:{source}" for key, source in step.bind.items())
    line += _each_text(step)
    parameters = _step_parameters(prepared, step)
    if parameters:
        line += " parameters=" + ",".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in parameters.items()
        )
    return line


def _each_text(step: WorkflowStep) -> str:
    if step.each is None:
        return ""
    text = f" each={step.each} split={step.split}"
    text += "" if step.as_parameter is None else f" as={step.as_parameter}"
    return text + f" max_parallel={step.max_parallel}"


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
