from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import pytest

from pratfall.config import load_config
from pratfall.models import Workflow, WorkflowStep
from pratfall.workflow_plan import (
    StepOutcome,
    early_result,
    final_steps,
    join_outputs,
    ready_steps,
    step_arguments,
    step_input,
    step_parameters,
    synthesized_result,
    workflow_parameters,
    workflow_result,
)


def step(name: str, selector: str, **bind: str) -> WorkflowStep:
    return WorkflowStep(name=name, selector=selector, after=(), bind=bind)


def test_parameters_follow_first_appearance_across_steps() -> None:
    workflow = Workflow(
        steps=(step("one", "a"), step("two", "b"), step("three", "c")), source=Path("c.toml")
    )
    prompts = {"a": "Fix $zeta then $alpha $input", "b": "Use ${alpha} and $beta", "c": None}
    assert workflow_parameters(workflow, prompts) == ("zeta", "alpha", "beta")


def test_bound_names_are_excluded_only_for_the_binding_step() -> None:
    workflow = Workflow(
        steps=(step("one", "a"), step("two", "b", report="one"), step("three", "b")),
        source=Path("c.toml"),
    )
    prompts = {"a": "Do $folder", "b": "Check $report in $folder"}
    assert workflow_parameters(workflow, prompts) == ("folder", "report")


def test_bound_name_used_nowhere_else_is_not_a_parameter() -> None:
    workflow = Workflow(steps=(step("one", "a"), step("two", "b", report="one")), source=Path("c"))
    assert workflow_parameters(workflow, {"a": None, "b": "Check $report"}) == ()


def test_parameters_use_prompt_inherited_through_extends(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        'version=1\n[profiles.base]\nagent="codex"\nprompt="Do $thing"\n'
        '[profiles.child]\nextends="base"\n'
        '[workflows.w]\nsteps=[{name="x",profile="child"}]\n',
        encoding="utf-8",
    )
    config = load_config(path)
    prompt = config.profiles["child"].prompt
    assert prompt is not None
    assert workflow_parameters(config.workflows["w"], {"child": prompt.prompt}) == ("thing",)


def node(
    name: str,
    *after: str,
    input: Literal["upstream", "none"] = "upstream",
    bind: Mapping[str, str] | None = None,
) -> WorkflowStep:
    return WorkflowStep(name=name, selector=f"p-{name}", after=after, input=input, bind=bind or {})


def graph(*steps: WorkflowStep, parallel: bool = True) -> Workflow:
    return Workflow(steps=steps, source=Path("c.toml"), parallel=parallel)


CHAIN = graph(node("a"), node("b", "a"), node("c", "b"))
FAN = graph(node("a"), node("b", "a"), node("c", "a"), node("d", "b", "c"))
SPLIT = graph(node("a"), node("b", "a"), node("c", "a"))


def test_one_block_is_passed_without_heading() -> None:
    assert join_outputs((("a", "X\n"),)) == "X"


def test_two_blocks_are_joined_with_step_headings() -> None:
    assert join_outputs((("a", "X\n"), ("b", "Y"))) == "## a\n\nX\n\n## b\n\nY"


def test_only_one_trailing_newline_is_removed() -> None:
    assert join_outputs((("a", "X\n\n"),)) == "X\n"


def test_blank_blocks_are_dropped() -> None:
    assert join_outputs((("a", " \n"), ("b", "Y\n"), ("c", ""))) == "Y"


def test_all_blank_blocks_join_to_none() -> None:
    assert join_outputs((("a", "\n"), ("b", " \t"))) is None


@pytest.mark.parametrize("base", ["notes", None])
def test_root_step_receives_the_base_input(base: str | None) -> None:
    assert step_input(node("a"), {}, base) == base


def test_input_none_receives_nothing() -> None:
    assert step_input(node("b", "a", input="none"), {"a": "X"}, "base") is None
    assert step_input(node("a", input="none"), {}, "base") is None


def test_upstream_outputs_join_in_after_order() -> None:
    outputs = {"a": "A\n", "b": "B\n"}
    assert step_input(node("c", "b", "a"), outputs, "base") == "## b\n\nB\n\n## a\n\nA"


def test_bind_consumed_upstream_is_excluded_from_input() -> None:
    step = node("c", "a", "b", bind={"report": "b"})
    assert step_input(step, {"a": "A\n", "b": "B\n"}, None) == "A"
    only_bound = node("c", "b", bind={"report": "b"})
    assert step_input(only_bound, {"b": "B"}, None) is None


def test_step_arguments_pass_only_the_step_parameters() -> None:
    step = node("c", "a", bind={"report": "a"})
    arguments = step_arguments(
        step,
        "Check $report in $folder, $folder",
        {"report": "user", "folder": "src", "level": "3"},
        {"a": "found 2\n"},
        ("--cwd", "repo", "--timeout", "60"),
    )
    assert arguments == (
        "p-c",
        "--json",
        "--no-notify",
        "--cwd",
        "repo",
        "--timeout",
        "60",
        "--param",
        "report=found 2",
        "--param",
        "folder=src",
    )


def test_step_parameters_are_the_user_values_of_unbound_names() -> None:
    step = node("c", "a", bind={"report": "a"})
    values = {"report": "user", "folder": "src", "level": "3"}
    assert step_parameters(step, "Check $report in $folder", values) == {"folder": "src"}
    assert step_parameters(step, None, values) == {}


def test_early_result_has_the_workflow_shape_without_steps() -> None:
    assert early_result("tidy", "error", "invalid_arguments", 2, "bad flag") == {
        "schema_version": 1,
        "workflow": "tidy",
        "status": "error",
        "exit_code": 2,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": {"code": "invalid_arguments", "message": "bad flag"},
        "steps": [],
    }
    assert list(early_result("tidy", "error", "invalid_arguments", 2, "bad flag")) == [
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


def test_step_arguments_without_prompt_carry_no_parameters() -> None:
    assert step_arguments(node("a"), None, {"folder": "src"}, {}, ()) == (
        "p-a",
        "--json",
        "--no-notify",
    )


def names(steps: tuple[WorkflowStep, ...]) -> tuple[str, ...]:
    return tuple(step.name for step in steps)


def test_chain_runs_one_step_after_another() -> None:
    none: frozenset[str] = frozenset()
    assert names(ready_steps(CHAIN, none, none, none)) == ("a",)
    assert names(ready_steps(CHAIN, none, frozenset("a"), frozenset("a"))) == ()
    assert names(ready_steps(CHAIN, frozenset("a"), frozenset("a"), none)) == ("b",)


def test_fan_out_and_merge_wait_for_their_after_steps() -> None:
    a = frozenset("a")
    assert names(ready_steps(FAN, a, a, frozenset())) == ("b", "c")
    started = frozenset("abc")
    assert names(ready_steps(FAN, frozenset("ab"), started, frozenset("c"))) == ()
    assert names(ready_steps(FAN, frozenset("abc"), started, frozenset())) == ("d",)


def test_sequential_workflow_starts_one_step_in_declared_order() -> None:
    serial = graph(*FAN.steps, parallel=False)
    a = frozenset("a")
    assert names(ready_steps(serial, a, a, frozenset())) == ("b",)
    assert names(ready_steps(serial, a, frozenset("ab"), frozenset("b"))) == ()
    assert names(ready_steps(serial, frozenset("ab"), frozenset("ab"), frozenset())) == ("c",)


def test_final_steps_are_those_nothing_runs_after() -> None:
    assert final_steps(CHAIN) == ("c",)
    assert final_steps(SPLIT) == ("b", "c")
    assert final_steps(FAN) == ("d",)


def child(
    output: str = "",
    *,
    status: str = "success",
    exit_code: int = 0,
    error: dict[str, str] | None = None,
    usage: dict[str, int | None] | None = None,
    cost_usd: float | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": "codex",
        "status": status,
        "output": output,
        "exit_code": exit_code,
        "usage": usage,
        "cost_usd": cost_usd,
        "error": error,
    }


def outcome(step: str, result: Mapping[str, object]) -> StepOutcome:
    return StepOutcome(step=step, result=result)


def test_success_with_one_final_step() -> None:
    outcomes = (outcome("a", child("A\n")), outcome("b", child("B\n")), outcome("c", child("C\n")))
    result = workflow_result("tidy", CHAIN, outcomes, 1234, None)
    assert list(result) == [
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
    assert result["schema_version"] == 1
    assert result["workflow"] == "tidy"
    assert (result["status"], result["exit_code"], result["output"]) == ("success", 0, "C")
    assert result["duration_ms"] == 1234
    assert result["error"] is None
    assert result["steps"] == [
        {"step": "a", **child("A\n")},
        {"step": "b", **child("B\n")},
        {"step": "c", **child("C\n")},
    ]


def test_success_with_two_final_steps_joins_in_declared_order() -> None:
    outcomes = (outcome("a", child("A")), outcome("c", child("C\n")), outcome("b", child("B\n")))
    result = workflow_result("split", SPLIT, outcomes, 5, None)
    assert result["output"] == "## b\n\nB\n\n## c\n\nC"


def test_blank_final_output_is_empty_text() -> None:
    result = workflow_result("chain", CHAIN, tuple(outcome(n, child()) for n in "abc"), 5, None)
    assert result["output"] == ""


def test_first_failure_by_completion_order_decides_the_result() -> None:
    timeout = child(
        status="timeout", exit_code=124, error={"code": "timeout", "message": "Timed out."}
    )
    broken = child(
        status="error", exit_code=1, error={"code": "native_exit", "message": "Codex exited."}
    )
    outcomes = (outcome("a", child("A")), outcome("c", timeout), outcome("b", broken))
    result = workflow_result("fan", FAN, outcomes, 9, None)
    assert (result["status"], result["exit_code"], result["output"]) == ("timeout", 124, "")
    assert result["error"] == {"code": "timeout", "message": "step 'c': Timed out."}
    assert result["steps"] == [
        {"step": "a", **child("A")},
        {"step": "b", **broken},
        {"step": "c", **timeout},
        {"step": "d", "status": "skipped"},
    ]


def test_synthesized_failure_is_reported_like_a_child_failure() -> None:
    failure = synthesized_result("error", "internal_error", "Step result is not valid JSON.")
    result = workflow_result("chain", CHAIN, (outcome("a", failure),), 3, None)
    assert (result["status"], result["exit_code"]) == ("error", 1)
    assert result["error"] == {
        "code": "internal_error",
        "message": "step 'a': Step result is not valid JSON.",
    }
    assert result["usage"] is None
    assert result["cost_usd"] is None
    assert result["steps"] == [
        {"step": "a", **failure},
        {"step": "b", "status": "skipped"},
        {"step": "c", "status": "skipped"},
    ]


def test_failure_without_error_report_is_an_internal_error() -> None:
    result = workflow_result(
        "chain", CHAIN, (outcome("a", child(status="error", exit_code=1)),), 3, None
    )
    assert result["error"] == {
        "code": "internal_error",
        "message": "step 'a': failed without an error report.",
    }


def test_interruption_overrides_a_failure() -> None:
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    result = workflow_result("chain", CHAIN, (outcome("a", broken),), 7, 2)
    assert (result["status"], result["exit_code"], result["output"]) == ("interrupted", 130, "")
    assert result["error"] == {"code": "interrupted", "message": "Interrupted by signal 2."}


def test_interruption_overrides_a_success() -> None:
    outcomes = tuple(outcome(n, child(n.upper())) for n in "abc")
    result = workflow_result("chain", CHAIN, outcomes, 7, 15)
    assert (result["status"], result["exit_code"], result["output"]) == ("interrupted", 143, "")
    assert result["error"] == {"code": "interrupted", "message": "Interrupted by signal 15."}


def test_usage_and_cost_sum_reported_values() -> None:
    first = child(
        usage={
            "input_tokens": 10,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": 3,
            "reasoning_output_tokens": None,
        },
        cost_usd=0.25,
    )
    second = child(
        usage={
            "input_tokens": 5,
            "cached_input_tokens": 2,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
        },
        cost_usd=None,
    )
    third = child(usage=None, cost_usd=0.5)
    outcomes = (outcome("a", first), outcome("b", second), outcome("c", third))
    result = workflow_result("chain", CHAIN, outcomes, 1, None)
    assert result["usage"] == {
        "input_tokens": 15,
        "cached_input_tokens": 2,
        "cache_write_input_tokens": None,
        "output_tokens": 3,
        "reasoning_output_tokens": None,
    }
    assert result["cost_usd"] == 0.75


def test_cost_sums_in_declared_order_whatever_the_completion_order() -> None:
    costs = {"a": 0.1, "b": 0.2, "c": 0.3}
    forward = tuple(outcome(name, child(cost_usd=costs[name])) for name in ("a", "b", "c"))
    backward = tuple(reversed(forward))
    assert workflow_result("chain", CHAIN, forward, 1, None)["cost_usd"] == 0.6
    assert workflow_result("chain", CHAIN, backward, 1, None)["cost_usd"] == 0.6


def test_usage_and_cost_are_null_when_no_step_reports_them() -> None:
    result = workflow_result("chain", CHAIN, tuple(outcome(n, child()) for n in "abc"), 1, None)
    assert result["usage"] is None
    assert result["cost_usd"] is None
