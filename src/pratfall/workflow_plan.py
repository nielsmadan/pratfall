from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from typing import Any, assert_never

from pratfall.codes import INTERNAL_ERROR, INTERRUPTED, SIGNAL_EXIT_BASE, Code, exit_code_for
from pratfall.config import step_prompt
from pratfall.models import Config, JsonValue, ResultError, Usage, Workflow, WorkflowStep
from pratfall.prompt_templates import template_parameters

_USAGE_FIELDS = tuple(field.name for field in fields(Usage))


@dataclass(frozen=True)
class StepResult:
    status: str
    exit_code: int
    output: str
    duration_ms: int
    usage: Mapping[str, int | None] | None
    cost_usd: int | float | None
    error: ResultError | None
    structured_output_present: bool
    structured_output: JsonValue
    payload: Mapping[str, object]


@dataclass(frozen=True)
class StepOutcome:
    step: str
    result: StepResult
    item: int | None = None


def step_result(payload: Mapping[str, Any]) -> StepResult:
    error = payload["error"]
    duration_ms = payload.get("duration_ms")
    return StepResult(
        status=payload["status"],
        exit_code=payload["exit_code"],
        output=payload["output"],
        duration_ms=duration_ms if type(duration_ms) is int else 0,
        usage=payload["usage"],
        cost_usd=payload["cost_usd"],
        error=None if error is None else ResultError(error["code"], error["message"]),
        structured_output_present=payload.get("structured_output") is not None,
        structured_output=payload.get("structured_output"),
        payload=payload,
    )


def workflow_prompts(config: Config, workflow: Workflow) -> dict[str, str | None]:
    return {step.selector: step_prompt(config, step) for step in workflow.steps}


def workflow_parameter_names(config: Config, workflow: Workflow) -> tuple[str, ...]:
    return workflow_parameters(workflow, workflow_prompts(config, workflow))


def workflow_parameters(workflow: Workflow, prompts: Mapping[str, str | None]) -> tuple[str, ...]:
    names = (
        name
        for step in workflow.steps
        for name in _user_parameters(step, prompts.get(step.selector))
    )
    return tuple(dict.fromkeys(names))


def step_parameters(
    step: WorkflowStep, prompt: str | None, values: Mapping[str, str]
) -> dict[str, str]:
    return {name: values[name] for name in _user_parameters(step, prompt)}


def trimmed_output(output: str) -> str:
    return output.removesuffix("\n")


def join_outputs(blocks: tuple[tuple[str, str], ...]) -> str | None:
    present = tuple((step, trimmed_output(output)) for step, output in blocks if output.strip())
    if not present:
        return None
    if len(present) == 1:
        return present[0][1]
    return "\n\n".join(f"## {step}\n\n{text}" for step, text in present)


def step_input(step: WorkflowStep, outputs: Mapping[str, str], base: str | None) -> str | None:
    match step.input:
        case "none":
            return None
        case "upstream":
            if not step.after:
                return base
            bound = frozenset(step.bind.values())
            return join_outputs(
                tuple((name, outputs[name]) for name in step.after if name not in bound)
            )
        case _:
            assert_never(step.input)


def step_arguments(
    step: WorkflowStep,
    prompt: str | None,
    values: Mapping[str, str],
    outputs: Mapping[str, str],
    passthrough: tuple[str, ...],
) -> tuple[str, ...]:
    parameters = tuple(
        argument
        for name in template_parameters(prompt or "")
        for argument in ("--param", f"{name}={_parameter_value(step, name, values, outputs)}")
    )
    return (step.selector, "--json", "--no-notify", *passthrough, *parameters)


def ready_steps(
    workflow: Workflow,
    succeeded: frozenset[str],
    started: frozenset[str],
    running: frozenset[str],
) -> tuple[WorkflowStep, ...]:
    ready = tuple(
        step
        for step in workflow.steps
        if step.name not in started and all(name in succeeded for name in step.after)
    )
    if workflow.parallel:
        return ready
    return () if running else ready[:1]


def final_steps(workflow: Workflow) -> tuple[str, ...]:
    upstream = frozenset(name for step in workflow.steps for name in step.after)
    return tuple(step.name for step in workflow.steps if step.name not in upstream)


def workflow_result(
    name: str,
    workflow: Workflow,
    outcomes: tuple[StepOutcome, ...],
    duration_ms: int,
    interrupted_by: int | None,
) -> dict[str, object]:
    results = {outcome.step: outcome.result for outcome in outcomes}
    declared = tuple(results[step.name] for step in workflow.steps if step.name in results)
    failure = next((outcome for outcome in outcomes if outcome.result.status != "success"), None)
    status, exit_code, output, error = _summary(workflow, results, failure, interrupted_by)
    return {
        "schema_version": 1,
        "workflow": name,
        "status": status,
        "exit_code": exit_code,
        "output": output,
        "duration_ms": duration_ms,
        "usage": _summed_usage(declared),
        "cost_usd": _sum_present(result.cost_usd for result in declared),
        "error": error,
        "steps": [
            {"step": step.name, **results[step.name].payload}
            if step.name in results
            else {"step": step.name, "status": "skipped"}
            for step in workflow.steps
        ],
    }


def synthesized_result(
    status: str, code: Code, message: str, exit_code: int | None = None
) -> StepResult:
    return step_result(_synthesized_payload(status, code, message, exit_code))


def _synthesized_payload(
    status: str, code: Code, message: str, exit_code: int | None
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": status,
        "exit_code": exit_code_for(code) if exit_code is None else exit_code,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": {"code": code, "message": message},
    }


def early_result(
    name: str, status: str, code: Code, exit_code: int, message: str
) -> dict[str, object]:
    failure = _synthesized_payload(status, code, message, exit_code)
    return {"schema_version": 1, "workflow": name} | failure | {"steps": []}


def _user_parameters(step: WorkflowStep, prompt: str | None) -> tuple[str, ...]:
    return tuple(name for name in template_parameters(prompt or "") if name not in step.bind)


def _parameter_value(
    step: WorkflowStep, name: str, values: Mapping[str, str], outputs: Mapping[str, str]
) -> str:
    source = step.bind.get(name)
    return values[name] if source is None else trimmed_output(outputs[source])


def _summary(
    workflow: Workflow,
    results: Mapping[str, StepResult],
    failure: StepOutcome | None,
    interrupted_by: int | None,
) -> tuple[str, int, str, dict[str, str] | None]:
    if interrupted_by is not None:
        message = f"Interrupted by signal {interrupted_by}."
        error = {"code": INTERRUPTED, "message": message}
        return "interrupted", SIGNAL_EXIT_BASE + interrupted_by, "", error
    if failure is not None:
        result = failure.result
        code, message = _error_fields(result.error)
        error = {"code": code, "message": f"step '{failure.step}': {message}"}
        return result.status, result.exit_code, "", error
    blocks = tuple(
        (step, results[step].output) for step in final_steps(workflow) if step in results
    )
    return "success", 0, join_outputs(blocks) or "", None


def _error_fields(error: ResultError | None) -> tuple[str, str]:
    if error is None:
        return INTERNAL_ERROR, "failed without an error report."
    return error.code, error.message


def _summed_usage(results: tuple[StepResult, ...]) -> dict[str, int | None] | None:
    reports = tuple(result.usage for result in results if result.usage is not None)
    if not reports:
        return None
    return {name: _sum_present(report.get(name) for report in reports) for name in _USAGE_FIELDS}


def _sum_present[T: (int, float)](values: Iterable[T | None]) -> T | None:
    present = tuple(value for value in values if value is not None)
    return sum(present[1:], present[0]) if present else None
