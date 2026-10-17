import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, fields, replace
from typing import Any, NamedTuple, assert_never

from pratfall.codes import (
    INTERNAL_ERROR,
    INTERRUPTED,
    INVALID_ARGUMENTS,
    SIGNAL_EXIT_BASE,
    STDOUT_LIMIT_EXCEEDED,
    Code,
    exit_code_for,
)
from pratfall.config import step_prompt
from pratfall.errors import PratError
from pratfall.fences import extract_code
from pratfall.limits import WORKFLOW_RESULT_BYTES
from pratfall.models import Config, JsonValue, ResultError, Usage, Workflow, WorkflowStep
from pratfall.prompt_input import PROMPT_LIMIT
from pratfall.prompt_templates import template_parameters

_USAGE_FIELDS = tuple(field.name for field in fields(Usage))
_LABEL_CHARACTERS = 80
_INDEX_LABEL = re.compile(r"#[0-9]+")


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
    return _upstream_input(step, outputs, base, ())


def instance_input(
    step: WorkflowStep, item: str, outputs: Mapping[str, str], base: str | None
) -> str | None:
    if step.as_parameter is None:
        return item
    return _upstream_input(step, outputs, base, (step.each,))


def _upstream_input(
    step: WorkflowStep,
    outputs: Mapping[str, str],
    base: str | None,
    excluded: Collection[str | None],
) -> str | None:
    match step.input:
        case "none":
            return None
        case "upstream":
            if not step.after:
                return base
            skipped = frozenset((*step.bind.values(), *excluded))
            return join_outputs(
                tuple((name, outputs[name]) for name in step.after if name not in skipped)
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


def instance_arguments(
    step: WorkflowStep,
    prompt: str | None,
    item: str,
    values: Mapping[str, str],
    outputs: Mapping[str, str],
    passthrough: tuple[str, ...],
) -> tuple[str, ...]:
    if step.as_parameter is None:
        return step_arguments(step, prompt, values, outputs, passthrough)
    return step_arguments(step, prompt, {**values, step.as_parameter: item}, outputs, passthrough)


def input_problem(
    step: WorkflowStep,
    prompt: str | None,
    text: str | None,
    outputs: Mapping[str, str],
    item: str | None = None,
) -> str | None:
    if prompt is None and text is None:
        return "has no prompt and received no input."
    empty = next((name for name, source in step.bind.items() if not outputs[source].strip()), None)
    if empty is not None:
        return f"binds parameter '{empty}' to the empty output of step '{step.bind[empty]}'."
    parameter = () if item is None or step.as_parameter is None else (item,)
    bound = tuple(trimmed_output(outputs[source]) for source in step.bind.values())
    values = (text or "", *parameter, *bound)
    if sum(len(value.encode("utf-8")) for value in values) > PROMPT_LIMIT:
        return f"input and bound parameters exceed the {PROMPT_LIMIT} byte limit."
    return None


class EachItems(NamedTuple):
    items: tuple[str, ...]
    dropped: int


def each_items(step: WorkflowStep, source: StepResult) -> EachItems:
    match step.split:
        case "lines":
            items = _line_items(source.output)
        case "json":
            items = _json_items(step.each, source)
        case _:
            assert_never(step.split)
    if len(items) <= step.max_items:
        return EachItems(items, 0)
    match step.overflow:
        case "truncate":
            return EachItems(items[: step.max_items], len(items) - step.max_items)
        case "error":
            message = (
                f"output of step '{step.each}' has {len(items)} items, "
                f"more than the {step.max_items} item limit."
            )
            raise PratError(message, code=INVALID_ARGUMENTS)
        case _:
            assert_never(step.overflow)


def item_labels(items: tuple[str, ...]) -> tuple[str, ...]:
    counts = Counter(items)
    return tuple(
        item if counts[item] == 1 and _labelable(item) else f"#{index}"
        for index, item in enumerate(items, 1)
    )


def each_result(
    step: WorkflowStep,
    items: tuple[str, ...],
    instances: tuple[StepOutcome, ...],
    complete: bool,
    dropped: int,
) -> StepResult:
    labels = item_labels(items)
    indexed = tuple(
        (outcome.item, outcome.result) for outcome in instances if outcome.item is not None
    )
    finished = dict(sorted(indexed, key=lambda pair: pair[0]))
    failure = next(((index, result) for index, result in indexed if not _succeeded(result)), None)
    reports = tuple(finished.values())
    aggregate = StepResult(
        status="success",
        exit_code=0,
        output="",
        duration_ms=sum(result.duration_ms for result in reports),
        usage=_summed_usage(reports),
        cost_usd=_sum_present(result.cost_usd for result in reports),
        error=None,
        structured_output_present=False,
        structured_output=None,
        payload={},
    )
    if failure is not None:
        index, failed = failure
        code, message = _error_fields(failed.error)
        error = ResultError(code, f"item '{labels[index]}': {message}")
        summary = replace(aggregate, status=failed.status, exit_code=failed.exit_code, error=error)
    elif not complete:
        summary = replace(aggregate, status="skipped")
    else:
        blocks = tuple(
            (f"{step.name}[{labels[index]}]", finished[index].output) for index in finished
        )
        summary = _joined(aggregate, join_outputs(blocks) or "")
    entries = [
        _item_entry(item, labels[index], finished.get(index)) for index, item in enumerate(items)
    ]
    return _with_items(summary, entries, dropped)


def ready_work(
    workflow: Workflow,
    items: Mapping[str, tuple[str, ...]],
    succeeded: AbstractSet[str],
    started: AbstractSet[str],
    launched: Mapping[str, int],
    running: Mapping[str, int],
) -> tuple[tuple[WorkflowStep, int | None], ...]:
    ready = tuple(
        work
        for step in workflow.steps
        if all(name in succeeded for name in step.after)
        for work in _step_work(step, items, started, launched, running)
    )
    if workflow.parallel:
        return ready
    return () if any(running.values()) else ready[:1]


def final_steps(workflow: Workflow) -> tuple[str, ...]:
    upstream = frozenset(name for step in workflow.steps for name in step.after)
    return tuple(step.name for step in workflow.steps if step.name not in upstream)


def workflow_result(
    name: str,
    workflow: Workflow,
    outcomes: tuple[StepOutcome, ...],
    duration_ms: int,
    interrupted_by: int | None,
    items: Mapping[str, EachItems],
) -> dict[str, object]:
    direct = {outcome.step: outcome.result for outcome in outcomes if outcome.item is None}
    results = {
        step.name: result
        for step in workflow.steps
        if (result := _step_outcome(step, direct, outcomes, items)) is not None
    }
    declared = _reported(workflow, outcomes)
    failure = _first_failure(workflow, outcomes, results)
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
            else _skipped_entry(step)
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


def _line_items(output: str) -> tuple[str, ...]:
    lines = (line.strip() for line in trimmed_output(output).split("\n"))
    return tuple(line for line in lines if line)


def _json_items(source: str | None, result: StepResult) -> tuple[str, ...]:
    structured = result.structured_output
    value = structured if isinstance(structured, list) else _parsed(source, result.output)
    if not isinstance(value, list):
        raise PratError(f"output of step '{source}' is not a JSON array.", code=INVALID_ARGUMENTS)
    try:
        texts = tuple(_encodable(_item_text(element)) for element in value)
    except ValueError:
        raise PratError(_invalid_json(source), code=INVALID_ARGUMENTS) from None
    return tuple(text for text in texts if text is not None)


def _parsed(source: str | None, output: str) -> object:
    for text in (output, extract_code(output)):
        try:
            return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
        except (ValueError, RecursionError):
            continue
    raise PratError(_invalid_json(source), code=INVALID_ARGUMENTS)


def _invalid_json(source: str | None) -> str:
    return f"output of step '{source}' is not valid JSON."


def _reject_constant(constant: str) -> object:
    raise ValueError(f"nonfinite number {constant}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"nonfinite number {text}")
    return value


def _item_text(element: object) -> str | None:
    if element is None:
        return None
    if isinstance(element, str):
        return element if element.strip() else None
    if isinstance(element, list | dict):
        return json.dumps(element, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return json.dumps(element, allow_nan=False)


def _encodable(text: str | None) -> str | None:
    if text is not None:
        text.encode("utf-8")
    return text


def _labelable(item: str) -> bool:
    return (
        len(item) <= _LABEL_CHARACTERS
        and _INDEX_LABEL.fullmatch(item) is None
        and item.splitlines() == [item]
        and all(unicodedata.category(character) != "Cc" for character in item)
    )


def _succeeded(result: StepResult) -> bool:
    return result.status == "success"


def _joined(aggregate: StepResult, output: str) -> StepResult:
    if not _oversized(output):
        return replace(aggregate, output=output)
    error = ResultError(STDOUT_LIMIT_EXCEEDED, _join_limit_message())
    exit_code = exit_code_for(STDOUT_LIMIT_EXCEEDED)
    return replace(aggregate, status="error", exit_code=exit_code, error=error)


def _oversized(output: str) -> bool:
    return len(output.encode("utf-8")) > WORKFLOW_RESULT_BYTES


def _join_limit_message() -> str:
    return f"joined output exceeds the {WORKFLOW_RESULT_BYTES} byte limit."


def _skipped_entry(step: WorkflowStep) -> dict[str, object]:
    entry: dict[str, object] = {"step": step.name, "status": "skipped"}
    return entry if step.each is None else entry | {"items": []}


def _item_entry(item: str, label: str, result: StepResult | None) -> dict[str, object]:
    if result is None:
        return {"item": item, "label": label, "status": "skipped"}
    return {"item": item, "label": label, **result.payload}


def _with_items(result: StepResult, entries: list[dict[str, object]], dropped: int) -> StepResult:
    error = result.error
    payload = {
        "status": result.status,
        "exit_code": result.exit_code,
        "output": result.output,
        "duration_ms": result.duration_ms,
        "usage": result.usage,
        "cost_usd": result.cost_usd,
        "error": None if error is None else {"code": error.code, "message": error.message},
        "items": entries,
        "dropped": dropped,
    }
    return replace(result, payload=payload)


def _step_work(
    step: WorkflowStep,
    items: Mapping[str, tuple[str, ...]],
    started: AbstractSet[str],
    launched: Mapping[str, int],
    running: Mapping[str, int],
) -> tuple[tuple[WorkflowStep, int | None], ...]:
    if step.each is None or step.name not in items:
        return () if step.name in started else ((step, None),)
    first = launched.get(step.name, 0)
    slots = max(0, step.max_parallel - running.get(step.name, 0))
    last = min(first + slots, len(items[step.name]))
    return tuple((step, index) for index in range(first, last))


def _step_outcome(
    step: WorkflowStep,
    direct: Mapping[str, StepResult],
    outcomes: tuple[StepOutcome, ...],
    items: Mapping[str, EachItems],
) -> StepResult | None:
    if step.each is None:
        return direct.get(step.name)
    if step.name in direct:
        return _with_items(direct[step.name], [], 0)
    if step.name not in items:
        return None
    instances = _instances(step.name, outcomes)
    listed = items[step.name]
    complete = len(instances) == len(listed.items)
    return each_result(step, listed.items, instances, complete, listed.dropped)


def _instances(name: str, outcomes: tuple[StepOutcome, ...]) -> tuple[StepOutcome, ...]:
    return tuple(
        outcome for outcome in outcomes if outcome.step == name and outcome.item is not None
    )


def _reported(workflow: Workflow, outcomes: tuple[StepOutcome, ...]) -> tuple[StepResult, ...]:
    return tuple(
        outcome.result
        for step in workflow.steps
        for outcome in sorted(
            (outcome for outcome in outcomes if outcome.step == step.name), key=_item_order
        )
    )


def _item_order(outcome: StepOutcome) -> int:
    return -1 if outcome.item is None else outcome.item


def _first_failure(
    workflow: Workflow, outcomes: tuple[StepOutcome, ...], results: Mapping[str, StepResult]
) -> StepOutcome | None:
    failing = tuple(
        (_failure_position(step.name, outcomes), declared, step.name)
        for declared, step in enumerate(workflow.steps)
        if step.name in results and _step_failed(step, results[step.name])
    )
    if not failing:
        return None
    _, _, name = min(failing)
    return StepOutcome(name, results[name])


def _step_failed(step: WorkflowStep, result: StepResult) -> bool:
    partial = step.each is not None and result.status == "skipped"
    return not _succeeded(result) and not partial


def _failure_position(name: str, outcomes: tuple[StepOutcome, ...]) -> int:
    positions = tuple(index for index, outcome in enumerate(outcomes) if outcome.step == name)
    failed = (index for index in positions if not _succeeded(outcomes[index].result))
    return next(failed, positions[-1])


def _user_parameters(step: WorkflowStep, prompt: str | None) -> tuple[str, ...]:
    return tuple(
        name
        for name in template_parameters(prompt or "")
        if name not in step.bind and name != step.as_parameter
    )


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
    output = join_outputs(blocks) or ""
    if _oversized(output):
        error = {"code": STDOUT_LIMIT_EXCEEDED, "message": f"final output: {_join_limit_message()}"}
        return "error", exit_code_for(STDOUT_LIMIT_EXCEEDED), "", error
    return "success", 0, output, None


def _error_fields(error: ResultError | None) -> tuple[Code, str]:
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
    total = sum(present[1:], present[0]) if present else None
    return None if isinstance(total, float) and not math.isfinite(total) else total
