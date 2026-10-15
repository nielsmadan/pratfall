from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from pratfall.config import load_config
from pratfall.errors import PratError
from pratfall.limits import STDOUT_BYTES, WORKFLOW_EACH_ITEMS, WORKFLOW_RESULT_BYTES
from pratfall.models import ResultError, Workflow, WorkflowStep
from pratfall.prompt_input import PROMPT_LIMIT
from pratfall.workflow_plan import (
    EachItems,
    StepOutcome,
    StepResult,
    each_items,
    each_result,
    early_result,
    final_steps,
    input_problem,
    instance_arguments,
    instance_input,
    item_labels,
    join_outputs,
    ready_work,
    step_arguments,
    step_input,
    step_parameters,
    step_result,
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


def test_each_as_name_is_excluded_only_for_the_each_step() -> None:
    each = WorkflowStep(name="two", selector="b", after=("one",), each="one", as_parameter="file")
    workflow = Workflow(steps=(step("one", "a"), each, step("three", "b")), source=Path("c"))
    prompts = {"a": "List", "b": "Fix $file in $folder"}
    assert workflow_parameters(workflow, prompts) == ("folder", "file")
    each_only = Workflow(steps=(step("one", "a"), each), source=Path("c"))
    assert workflow_parameters(each_only, prompts) == ("folder",)


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


def ready(
    workflow: Workflow,
    succeeded: Collection[str] = (),
    started: Collection[str] = (),
    *,
    items: Mapping[str, tuple[str, ...]] | None = None,
    launched: Mapping[str, int] | None = None,
    running: Mapping[str, int] | None = None,
) -> tuple[tuple[str, int | None], ...]:
    pairs = ready_work(
        workflow,
        items or {},
        frozenset(succeeded),
        frozenset(started),
        launched or {},
        running or {},
    )
    return tuple((step.name, index) for step, index in pairs)


def test_chain_runs_one_step_after_another() -> None:
    assert ready(CHAIN) == (("a", None),)
    assert ready(CHAIN, (), {"a"}, running={"a": 1}) == ()
    assert ready(CHAIN, {"a"}, {"a"}) == (("b", None),)


def test_fan_out_and_merge_wait_for_their_after_steps() -> None:
    assert ready(FAN, {"a"}, {"a"}) == (("b", None), ("c", None))
    assert ready(FAN, {"a", "b"}, {"a", "b", "c"}, running={"c": 1}) == ()
    assert ready(FAN, {"a", "b", "c"}, {"a", "b", "c"}) == (("d", None),)


def test_sequential_workflow_starts_one_step_in_declared_order() -> None:
    serial = graph(*FAN.steps, parallel=False)
    assert ready(serial, {"a"}, {"a"}) == (("b", None),)
    assert ready(serial, {"a"}, {"a", "b"}, running={"b": 1}) == ()
    assert ready(serial, {"a", "b"}, {"a", "b"}) == (("c", None),)


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


def outcome(step: str, result: Mapping[str, object] | StepResult) -> StepOutcome:
    built = result if isinstance(result, StepResult) else step_result(result)
    return StepOutcome(step=step, result=built)


def test_success_with_one_final_step() -> None:
    outcomes = (outcome("a", child("A\n")), outcome("b", child("B\n")), outcome("c", child("C\n")))
    result = workflow_result("tidy", CHAIN, outcomes, 1234, None, {})
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
    result = workflow_result("split", SPLIT, outcomes, 5, None, {})
    assert result["output"] == "## b\n\nB\n\n## c\n\nC"


def test_blank_final_output_is_empty_text() -> None:
    result = workflow_result("chain", CHAIN, tuple(outcome(n, child()) for n in "abc"), 5, None, {})
    assert result["output"] == ""


def test_first_failure_by_completion_order_decides_the_result() -> None:
    timeout = child(
        status="timeout", exit_code=124, error={"code": "timeout", "message": "Timed out."}
    )
    broken = child(
        status="error", exit_code=1, error={"code": "native_exit", "message": "Codex exited."}
    )
    outcomes = (outcome("a", child("A")), outcome("c", timeout), outcome("b", broken))
    result = workflow_result("fan", FAN, outcomes, 9, None, {})
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
    result = workflow_result("chain", CHAIN, (outcome("a", failure),), 3, None, {})
    assert (result["status"], result["exit_code"]) == ("error", 1)
    assert result["error"] == {
        "code": "internal_error",
        "message": "step 'a': Step result is not valid JSON.",
    }
    assert result["usage"] is None
    assert result["cost_usd"] is None
    assert result["steps"] == [
        {"step": "a", **failure.payload},
        {"step": "b", "status": "skipped"},
        {"step": "c", "status": "skipped"},
    ]


def test_failure_without_error_report_is_an_internal_error() -> None:
    result = workflow_result(
        "chain", CHAIN, (outcome("a", child(status="error", exit_code=1)),), 3, None, {}
    )
    assert result["error"] == {
        "code": "internal_error",
        "message": "step 'a': failed without an error report.",
    }


def test_interruption_overrides_a_failure() -> None:
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    result = workflow_result("chain", CHAIN, (outcome("a", broken),), 7, 2, {})
    assert (result["status"], result["exit_code"], result["output"]) == ("interrupted", 130, "")
    assert result["error"] == {"code": "interrupted", "message": "Interrupted by signal 2."}


def test_interruption_overrides_a_success() -> None:
    outcomes = tuple(outcome(n, child(n.upper())) for n in "abc")
    result = workflow_result("chain", CHAIN, outcomes, 7, 15, {})
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
    result = workflow_result("chain", CHAIN, outcomes, 1, None, {})
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
    assert workflow_result("chain", CHAIN, forward, 1, None, {})["cost_usd"] == 0.6
    assert workflow_result("chain", CHAIN, backward, 1, None, {})["cost_usd"] == 0.6


def test_usage_and_cost_are_null_when_no_step_reports_them() -> None:
    result = workflow_result("chain", CHAIN, tuple(outcome(n, child()) for n in "abc"), 1, None, {})
    assert result["usage"] is None
    assert result["cost_usd"] is None


def test_step_result_reports_structured_output_only_when_present() -> None:
    absent = step_result({**child(output="plain"), "structured_output": None})
    present = step_result({**child(output="[]"), "structured_output": ["a", "b"]})
    assert (absent.structured_output_present, absent.structured_output) == (False, None)
    assert (present.structured_output_present, present.structured_output) == (True, ["a", "b"])


def fan(
    name: str,
    source: str,
    *after: str,
    split: Literal["lines", "json"] = "lines",
    as_parameter: str | None = None,
    max_parallel: int = 4,
    input: Literal["upstream", "none"] = "upstream",
) -> WorkflowStep:
    return WorkflowStep(
        name=name,
        selector=f"p-{name}",
        after=(source, *after),
        input=input,
        each=source,
        split=split,
        as_parameter=as_parameter,
        max_parallel=max_parallel,
    )


def source(output: str, structured: object = None) -> StepResult:
    return step_result({**child(output), "structured_output": structured})


def items_error(step: WorkflowStep, result: StepResult) -> tuple[str, str]:
    with pytest.raises(PratError) as caught:
        each_items(step, result)
    return caught.value.code, str(caught.value)


def test_lines_drop_one_trailing_newline_whitespace_and_blank_lines() -> None:
    result = source("  a.py \n\nb.py\r\n \n\tc.py\n\n")
    assert each_items(fan("fix", "list"), result).items == ("a.py", "b.py", "c.py")


def test_json_items_come_from_a_structured_output_list() -> None:
    result = source("not json", ["x.py", 1])
    assert each_items(fan("fix", "list", split="json"), result).items == ("x.py", "1")


def test_json_items_parse_the_output_when_structured_output_is_not_a_list() -> None:
    result = source('["a", "b"]\n', {"files": ["x"]})
    assert each_items(fan("fix", "list", split="json"), result).items == ("a", "b")


def test_empty_structured_output_list_wins_over_an_array_in_the_output() -> None:
    assert each_items(fan("fix", "list", split="json"), source('["a", "b"]', [])).items == ()


@pytest.mark.parametrize(
    "structured", [[float("inf")], [float("nan")], [{"a": [float("-inf")]}], ["a", [1e400]]]
)
def test_structured_items_reject_nonfinite_numbers(structured: object) -> None:
    assert items_error(fan("fix", "list", split="json"), source("[]", structured)) == (
        "invalid_arguments",
        "output of step 'list' is not valid JSON.",
    )


def test_json_items_parse_plain_output_text() -> None:
    assert each_items(fan("fix", "list", split="json"), source('["a", "b"]\n')).items == ("a", "b")


def test_json_items_fall_back_to_the_first_fenced_block() -> None:
    output = 'Files:\n```json\n["a", "b"]\n```\n```json\n["c"]\n```\n'
    assert each_items(fan("fix", "list", split="json"), source(output)).items == ("a", "b")


def test_json_elements_become_item_text_and_null_or_blank_is_dropped() -> None:
    output = '["s", 1, 2.5, true, false, {"k": "\u00e9", "n": [1]}, [1, 2], null, "  ", "", " t "]'
    assert each_items(fan("fix", "list", split="json"), source(output)).items == (
        "s",
        "1",
        "2.5",
        "true",
        "false",
        '{"k":"\u00e9","n":[1]}',
        "[1,2]",
        " t ",
    )


@pytest.mark.parametrize("output", ["[NaN]", "[Infinity]", "[-Infinity]", "[1e400]"])
def test_json_items_reject_nonfinite_numbers(output: str) -> None:
    assert items_error(fan("fix", "list", split="json"), source(output)) == (
        "invalid_arguments",
        "output of step 'list' is not valid JSON.",
    )


@pytest.mark.parametrize(
    ("output", "structured"),
    [('["ok", "\\ud800"]', None), ('[{"k": "\\udfff"}]', None), ("[]", ["ok", "\ud800"])],
)
def test_json_items_reject_unpaired_surrogates(output: str, structured: object) -> None:
    assert items_error(fan("fix", "list", split="json"), source(output, structured)) == (
        "invalid_arguments",
        "output of step 'list' is not valid JSON.",
    )


@pytest.mark.parametrize("output", ["not json", "```json\n[1,\n```", "[" * 100_000])
def test_unparseable_json_output_is_an_items_error(output: str) -> None:
    assert items_error(fan("fix", "list", split="json"), source(output)) == (
        "invalid_arguments",
        "output of step 'list' is not valid JSON.",
    )


@pytest.mark.parametrize(
    ("output", "structured"), [('{"a": 1}', None), ("```\n42\n```", None), ('"x"', {"a": 1})]
)
def test_json_output_that_is_not_an_array_is_an_items_error(
    output: str, structured: object
) -> None:
    assert items_error(fan("fix", "list", split="json"), source(output, structured)) == (
        "invalid_arguments",
        "output of step 'list' is not a JSON array.",
    )


def test_item_limit_counts_items_after_dropping_null_and_blank() -> None:
    at_limit = ["x"] * WORKFLOW_EACH_ITEMS + [None, ""]
    result = source("[]", at_limit)
    assert len(each_items(fan("fix", "list", split="json"), result).items) == WORKFLOW_EACH_ITEMS
    over = source("\n".join(["x"] * (WORKFLOW_EACH_ITEMS + 1)))
    assert items_error(fan("fix", "list"), over) == (
        "invalid_arguments",
        (
            f"output of step 'list' has {WORKFLOW_EACH_ITEMS + 1} items, "
            f"more than the {WORKFLOW_EACH_ITEMS} item limit."
        ),
    )


def test_labels_are_short_unique_single_line_items_otherwise_the_index() -> None:
    items = ("a.py", "x" * 80, "y" * 81, "two\nlines", "tab\there", "bell\x07", "dup", "dup", "#9")
    assert item_labels(items) == ("a.py", "x" * 80, "#3", "#4", "#5", "#6", "#7", "#8", "#9")


def test_items_that_look_like_index_labels_get_their_own_index() -> None:
    items = ("#2", "y" * 81, "#07", "#x", "a#1")
    assert item_labels(items) == ("#1", "#2", "#3", "#x", "a#1")


def test_instance_with_as_gets_upstream_input_without_the_each_source() -> None:
    step = fan("fix", "list", "notes", as_parameter="file")
    outputs = {"list": "a.py\n", "notes": "N\n"}
    assert instance_input(step, "a.py", outputs, "base") == "N"
    assert instance_input(fan("fix", "list", as_parameter="file"), "a.py", outputs, None) is None
    silent = fan("fix", "list", "notes", as_parameter="file", input="none")
    assert instance_input(silent, "a.py", outputs, "base") is None


def test_instance_without_as_gets_the_item_as_its_whole_input() -> None:
    outputs = {"list": "a.py\n", "notes": "N\n"}
    assert instance_input(fan("fix", "list", "notes"), "a.py", outputs, "base") == "a.py"


def test_instance_arguments_bind_the_item_to_the_as_parameter() -> None:
    step = fan("fix", "list", as_parameter="file")
    arguments = instance_arguments(
        step, "Fix $file in $folder", "a.py", {"folder": "src"}, {"list": "a.py\n"}, ("--x",)
    )
    assert arguments == (
        "p-fix",
        "--json",
        "--no-notify",
        "--x",
        "--param",
        "file=a.py",
        "--param",
        "folder=src",
    )


def test_instance_arguments_without_as_carry_only_the_user_parameters() -> None:
    arguments = instance_arguments(
        fan("fix", "list"), "Fix in $folder", "a.py", {"folder": "src"}, {"list": "a.py"}, ()
    )
    assert arguments == ("p-fix", "--json", "--no-notify", "--param", "folder=src")


def test_prompt_limit_counts_the_as_item() -> None:
    step = fan("fix", "list", as_parameter="file")
    text = "x" * (PROMPT_LIMIT - 3)
    outputs = {"list": "a\n"}
    assert input_problem(step, "Fix $file", text, outputs, "abc") is None
    assert input_problem(step, "Fix $file", text, outputs, "abcd") == (
        f"input and bound parameters exceed the {PROMPT_LIMIT} byte limit."
    )


def test_prompt_limit_counts_an_item_without_as_once() -> None:
    item = "x" * PROMPT_LIMIT
    assert input_problem(fan("fix", "list"), None, item, {"list": "a\n"}, item) is None


QUEUE_ITEMS = {"fix": ("x", "y", "z")}
LISTED = {"list"}


def fixes(
    workflow: Workflow, items: Mapping[str, tuple[str, ...]], launched: int = 0, running: int = 0
) -> tuple[tuple[str, int | None], ...]:
    launches, active = {"fix": launched}, {"fix": running}
    return ready(workflow, LISTED, LISTED, items=items, launched=launches, running=active)


def test_ready_work_honours_max_parallel_two() -> None:
    workflow = graph(node("list"), fan("fix", "list", max_parallel=2))
    assert fixes(workflow, QUEUE_ITEMS) == (("fix", 0), ("fix", 1))
    assert fixes(workflow, QUEUE_ITEMS, 1, 1) == (("fix", 1),)
    assert fixes(workflow, QUEUE_ITEMS, 2, 2) == ()
    assert fixes(workflow, QUEUE_ITEMS, 2, 1) == (("fix", 2),)


def test_ready_work_honours_max_parallel_one() -> None:
    workflow = graph(node("list"), fan("fix", "list", max_parallel=1))
    assert fixes(workflow, QUEUE_ITEMS) == (("fix", 0),)
    assert fixes(workflow, QUEUE_ITEMS, 1, 1) == ()
    assert fixes(workflow, QUEUE_ITEMS, 1) == (("fix", 1),)


def test_ready_work_starts_other_steps_alongside_instances() -> None:
    workflow = graph(node("list"), fan("fix", "list", max_parallel=2), node("check", "list"))
    assert ready(workflow, LISTED, LISTED, items=QUEUE_ITEMS) == (
        ("fix", 0),
        ("fix", 1),
        ("check", None),
    )


def test_sequential_workflow_runs_declared_steps_then_items_one_at_a_time() -> None:
    workflow = graph(node("list"), fan("fix", "list"), node("check", "list"), parallel=False)
    items = {"fix": ("x", "y")}
    assert fixes(workflow, items) == (("fix", 0),)
    assert fixes(workflow, items, 1, 1) == ()
    assert fixes(workflow, items, 1) == (("fix", 1),)
    assert fixes(workflow, items, 2) == (("check", None),)


def test_sequential_workflow_runs_a_ready_step_declared_before_the_each_step_first() -> None:
    workflow = graph(node("list"), node("check", "list"), fan("fix", "list"), parallel=False)
    items = {"fix": ("x", "y")}
    assert ready(workflow, LISTED, LISTED, items=items) == (("check", None),)
    checking = {"list", "check"}
    assert ready(workflow, LISTED, checking, items=items, running={"check": 1}) == ()
    assert ready(workflow, checking, checking, items=items) == (("fix", 0),)


def test_ready_work_with_many_items_takes_the_next_free_indices() -> None:
    workflow = graph(node("list"), fan("fix", "list", max_parallel=3))
    items = {"fix": tuple(str(index) for index in range(WORKFLOW_EACH_ITEMS))}
    assert fixes(workflow, items, 4093, 2) == (("fix", 4093),)
    assert fixes(workflow, items, 4093, 1) == (("fix", 4093), ("fix", 4094))
    assert fixes(workflow, items, WORKFLOW_EACH_ITEMS) == ()


def test_each_step_without_items_is_ready_as_a_whole_once_its_source_succeeded() -> None:
    workflow = graph(node("list"), fan("fix", "list"))
    assert ready(workflow) == (("list", None),)
    assert ready(workflow, LISTED, LISTED) == (("fix", None),)


def test_each_step_whose_items_failed_is_not_ready_again() -> None:
    workflow = graph(node("list"), fan("fix", "list"))
    assert ready(workflow, LISTED, {"list", "fix"}) == ()


def test_empty_item_list_launches_nothing_and_succeeds_with_empty_output() -> None:
    workflow = graph(node("list"), fan("fix", "list"), node("merge", "fix"))
    assert ready(workflow, LISTED, LISTED, items={"fix": ()}) == ()
    result = each_result(workflow.steps[1], (), (), True, 0)
    assert (result.status, result.exit_code, result.output, result.error) == (
        "success",
        0,
        "",
        None,
    )
    assert result.payload["items"] == []
    assert ready(workflow, {"list", "fix"}, LISTED, items={"fix": ()}) == (("merge", None),)


def instance(
    index: int, result: Mapping[str, object] | StepResult, step: str = "fix"
) -> StepOutcome:
    built = result if isinstance(result, StepResult) else step_result(result)
    return StepOutcome(step=step, result=built, item=index)


def tokens(count: int) -> dict[str, int | None]:
    return {
        "input_tokens": count,
        "cached_input_tokens": None,
        "cache_write_input_tokens": None,
        "output_tokens": 1,
        "reasoning_output_tokens": None,
    }


def timed(output: str, duration_ms: int, **fields: object) -> dict[str, object]:
    return {**child(output), "duration_ms": duration_ms, **fields}


def test_each_result_joins_outputs_in_item_order_with_labelled_headings() -> None:
    items = ("x.py", "y" * 81, "z.py")
    instances = (
        instance(2, timed("Z\n", 30, usage=tokens(3), cost_usd=0.25)),
        instance(0, timed("X\n", 10, usage=tokens(1), cost_usd=0.5)),
        instance(1, timed(" \n", 5)),
    )
    result = each_result(fan("fix", "list"), items, instances, True, 0)
    assert result.payload == {
        "status": "success",
        "exit_code": 0,
        "output": "## fix[x.py]\n\nX\n\n## fix[z.py]\n\nZ",
        "duration_ms": 45,
        "usage": {
            "input_tokens": 4,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": 2,
            "reasoning_output_tokens": None,
        },
        "cost_usd": 0.75,
        "error": None,
        "items": [
            {"item": "x.py", "label": "x.py", **instances[1].result.payload},
            {"item": "y" * 81, "label": "#2", **instances[2].result.payload},
            {"item": "z.py", "label": "z.py", **instances[0].result.payload},
        ],
        "dropped": 0,
    }
    assert (result.status, result.output, result.duration_ms, result.cost_usd) == (
        "success",
        "## fix[x.py]\n\nX\n\n## fix[z.py]\n\nZ",
        45,
        0.75,
    )


def test_each_result_with_one_item_is_its_bare_output() -> None:
    result = each_result(fan("fix", "list"), ("x.py",), (instance(0, child("X\n")),), True, 0)
    assert result.output == "X"


def test_each_result_reports_the_first_failed_instance() -> None:
    timeout = child(
        status="timeout", exit_code=124, error={"code": "timeout", "message": "Timed out."}
    )
    broken = child(
        status="error", exit_code=1, error={"code": "native_exit", "message": "Codex exited."}
    )
    instances = (instance(0, child("A")), instance(3, broken), instance(1, timeout))
    result = each_result(fan("fix", "list"), ("a", "b", "c", "d"), instances, False, 0)
    assert (result.status, result.exit_code, result.output) == ("error", 1, "")
    assert result.error == ResultError("native_exit", "item 'd': Codex exited.")
    assert result.payload["error"] == {"code": "native_exit", "message": "item 'd': Codex exited."}
    assert result.payload["items"] == [
        {"item": "a", "label": "a", **child("A")},
        {"item": "b", "label": "b", **timeout},
        {"item": "c", "label": "c", "status": "skipped"},
        {"item": "d", "label": "d", **broken},
    ]


def test_each_result_of_a_partial_step_is_skipped() -> None:
    instances = (instance(0, child("A", usage=tokens(2), cost_usd=0.5)),)
    result = each_result(fan("fix", "list"), ("a", "b"), instances, False, 0)
    assert result.payload == {
        "status": "skipped",
        "exit_code": 0,
        "output": "",
        "duration_ms": 0,
        "usage": tokens(2),
        "cost_usd": 0.5,
        "error": None,
        "items": [
            {"item": "a", "label": "a", **child("A", usage=tokens(2), cost_usd=0.5)},
            {"item": "b", "label": "b", "status": "skipped"},
        ],
        "dropped": 0,
    }


def test_each_result_over_the_joined_output_cap_fails_the_step() -> None:
    block = "x" * STDOUT_BYTES
    items = tuple("abcdefgh")
    instances = tuple(instance(index, child(block)) for index in range(len(items)))
    result = each_result(fan("fix", "list"), items, instances, True, 0)
    assert (result.status, result.exit_code, result.output) == ("error", 1, "")
    assert result.error == ResultError(
        "stdout_limit_exceeded", f"joined output exceeds the {WORKFLOW_RESULT_BYTES} byte limit."
    )


def test_each_result_at_exactly_the_joined_output_cap_succeeds() -> None:
    block = "x" * WORKFLOW_RESULT_BYTES
    result = each_result(fan("fix", "list"), ("a",), (instance(0, child(block)),), True, 0)
    assert (result.status, result.exit_code, result.error) == ("success", 0, None)
    assert result.output == block


def test_each_result_one_byte_over_the_joined_output_cap_fails() -> None:
    block = "x" * (WORKFLOW_RESULT_BYTES + 1)
    result = each_result(fan("fix", "list"), ("a",), (instance(0, child(block)),), True, 0)
    assert (result.status, result.exit_code, result.output) == ("error", 1, "")
    assert result.error == ResultError(
        "stdout_limit_exceeded", f"joined output exceeds the {WORKFLOW_RESULT_BYTES} byte limit."
    )


FIXES = graph(node("list"), fan("fix", "list", as_parameter="file"), node("merge", "fix"))
FIX_ITEMS = {"fix": EachItems(("a", "b"), 0)}


def test_workflow_result_sums_instances_without_double_counting() -> None:
    outcomes = (
        outcome("list", child("a\nb\n", usage=tokens(1), cost_usd=0.25)),
        instance(1, child("B\n", usage=tokens(10), cost_usd=0.25)),
        instance(0, child("A\n", usage=tokens(100), cost_usd=0.25)),
        outcome("merge", child("M\n", usage=tokens(1000), cost_usd=0.25)),
    )
    result = workflow_result("fixes", FIXES, outcomes, 9, None, FIX_ITEMS)
    assert (result["status"], result["exit_code"], result["output"]) == ("success", 0, "M")
    assert result["usage"] == {**tokens(1111), "output_tokens": 4}
    assert result["cost_usd"] == 1.0
    steps = result["steps"]
    assert isinstance(steps, list)
    assert steps[1] == {
        "step": "fix",
        "status": "success",
        "exit_code": 0,
        "output": "## fix[a]\n\nA\n\n## fix[b]\n\nB",
        "duration_ms": 0,
        "usage": {**tokens(110), "output_tokens": 2},
        "cost_usd": 0.5,
        "error": None,
        "items": [
            {"item": "a", "label": "a", **child("A\n", usage=tokens(100), cost_usd=0.25)},
            {"item": "b", "label": "b", **child("B\n", usage=tokens(10), cost_usd=0.25)},
        ],
        "dropped": 0,
    }


def test_final_each_step_contributes_its_joined_output() -> None:
    workflow = graph(node("list"), fan("fix", "list"))
    outcomes = (outcome("list", child("a\nb")), instance(0, child("A")), instance(1, child("B")))
    result = workflow_result("fixes", workflow, outcomes, 1, None, FIX_ITEMS)
    assert result["output"] == "## fix[a]\n\nA\n\n## fix[b]\n\nB"


def test_workflow_result_reports_a_failed_instance_and_skips_the_rest() -> None:
    broken = child(
        status="error", exit_code=3, error={"code": "native_exit", "message": "Codex exited."}
    )
    outcomes = (outcome("list", child("a\nb")), instance(0, broken))
    result = workflow_result("fixes", FIXES, outcomes, 1, None, FIX_ITEMS)
    assert (result["status"], result["exit_code"], result["output"]) == ("error", 3, "")
    assert result["error"] == {
        "code": "native_exit",
        "message": "step 'fix': item 'a': Codex exited.",
    }
    steps = result["steps"]
    assert isinstance(steps, list)
    assert steps[1]["items"] == [
        {"item": "a", "label": "a", **broken},
        {"item": "b", "label": "b", "status": "skipped"},
    ]
    assert (steps[1]["status"], steps[1]["exit_code"]) == ("error", 3)
    assert steps[2] == {"step": "merge", "status": "skipped"}


def test_pre_launch_items_error_has_no_item_entries() -> None:
    failure = synthesized_result(
        "error", "invalid_arguments", "output of step 'list' is not a JSON array."
    )
    outcomes = (outcome("list", child("x")), outcome("fix", failure))
    result = workflow_result("fixes", FIXES, outcomes, 1, None, {})
    assert (result["status"], result["exit_code"]) == ("error", 2)
    assert result["error"] == {
        "code": "invalid_arguments",
        "message": "step 'fix': output of step 'list' is not a JSON array.",
    }
    steps = result["steps"]
    assert isinstance(steps, list)
    assert steps[1] == {
        "step": "fix",
        "status": "error",
        "exit_code": 2,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": {
            "code": "invalid_arguments",
            "message": "output of step 'list' is not a JSON array.",
        },
        "items": [],
        "dropped": 0,
    }


def test_each_step_cut_short_by_another_failure_is_partial() -> None:
    workflow = graph(node("list"), fan("fix", "list"), node("check", "list"))
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    outcomes = (outcome("list", child("a\nb")), instance(0, child("A")), outcome("check", broken))
    result = workflow_result("fixes", workflow, outcomes, 1, None, FIX_ITEMS)
    assert result["error"] == {"code": "native_exit", "message": "step 'check': x"}
    steps = result["steps"]
    assert isinstance(steps, list)
    assert steps[1] == {
        "step": "fix",
        "status": "skipped",
        "exit_code": 0,
        "output": "",
        "duration_ms": 0,
        "usage": None,
        "cost_usd": None,
        "error": None,
        "items": [
            {"item": "a", "label": "a", **child("A")},
            {"item": "b", "label": "b", "status": "skipped"},
        ],
        "dropped": 0,
    }


def test_first_failure_orders_instances_and_steps_by_completion() -> None:
    workflow = graph(node("list"), fan("fix", "list"), node("check", "list"))
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    timeout = child(status="timeout", exit_code=124, error={"code": "timeout", "message": "t"})
    outcomes = (
        outcome("list", child("a\nb")),
        instance(1, timeout),
        outcome("check", broken),
        instance(0, child("A")),
    )
    result = workflow_result("fixes", workflow, outcomes, 1, None, FIX_ITEMS)
    assert (result["status"], result["exit_code"]) == ("timeout", 124)
    assert result["error"] == {"code": "timeout", "message": "step 'fix': item 'b': t"}


def test_interrupted_fan_out_reports_the_signal_and_skips_queued_items() -> None:
    interrupted = synthesized_result("interrupted", "interrupted", "Interrupted by signal 2.", 130)
    outcomes = (
        outcome("list", child("a\nb\nc")),
        instance(0, child("A")),
        instance(1, interrupted),
    )
    items = {"fix": EachItems(("a", "b", "c"), 0)}
    result = workflow_result("fixes", FIXES, outcomes, 1, 2, items)
    assert (result["status"], result["exit_code"], result["output"]) == ("interrupted", 130, "")
    assert result["error"] == {"code": "interrupted", "message": "Interrupted by signal 2."}
    steps = result["steps"]
    assert isinstance(steps, list)
    assert (steps[1]["status"], steps[1]["exit_code"]) == ("interrupted", 130)
    assert steps[1]["error"] == {
        "code": "interrupted",
        "message": "item 'b': Interrupted by signal 2.",
    }
    assert steps[1]["items"] == [
        {"item": "a", "label": "a", **child("A")},
        {"item": "b", "label": "b", **interrupted.payload},
        {"item": "c", "label": "c", "status": "skipped"},
    ]


def test_output_cap_failure_counts_from_the_last_instance_completion() -> None:
    workflow = graph(node("list"), fan("fix", "list"), node("check", "list"))
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    items = tuple("abcdefgh")
    block = child("x" * STDOUT_BYTES)
    instances = tuple(instance(index, block) for index in range(len(items)))
    before = (outcome("list", child("a")), *instances, outcome("check", broken))
    after = (outcome("list", child("a")), *instances[:-1], outcome("check", broken), instances[-1])
    first = workflow_result("fixes", workflow, before, 1, None, {"fix": EachItems(items, 0)})
    second = workflow_result("fixes", workflow, after, 1, None, {"fix": EachItems(items, 0)})
    assert first["error"] == {
        "code": "stdout_limit_exceeded",
        "message": f"step 'fix': joined output exceeds the {WORKFLOW_RESULT_BYTES} byte limit.",
    }
    assert second["error"] == {"code": "native_exit", "message": "step 'check': x"}


CAPPED_ITEMS = {"fix": EachItems(tuple("abcdefgh"), 0)}
CAPPED = tuple(instance(index, child("x" * STDOUT_BYTES)) for index in range(8))


def test_workflow_result_reports_the_output_cap_as_the_each_step_failure() -> None:
    outcomes = (outcome("list", child("a")), *CAPPED)
    result = workflow_result("fixes", FIXES, outcomes, 1, None, CAPPED_ITEMS)
    message = f"joined output exceeds the {WORKFLOW_RESULT_BYTES} byte limit."
    assert (result["status"], result["exit_code"]) == ("error", 1)
    assert result["error"] == {"code": "stdout_limit_exceeded", "message": f"step 'fix': {message}"}
    steps = result["steps"]
    assert isinstance(steps, list)
    fix = steps[1]
    assert (fix["status"], fix["output"], fix["error"]) == (
        "error",
        "",
        {"code": "stdout_limit_exceeded", "message": message},
    )
    assert fix["items"] == [
        {"item": label, "label": label, **child("x" * STDOUT_BYTES)} for label in "abcdefgh"
    ]
    assert steps[2] == {"step": "merge", "status": "skipped"}


def test_workflow_result_reports_a_never_started_each_step_with_an_empty_item_list() -> None:
    broken = child(status="error", exit_code=1, error={"code": "native_exit", "message": "x"})
    result = workflow_result("fixes", FIXES, (outcome("list", broken),), 1, None, {})
    assert result["steps"] == [
        {"step": "list", **broken},
        {"step": "fix", "status": "skipped", "items": []},
        {"step": "merge", "status": "skipped"},
    ]


def capped(max_items: int, overflow: Literal["error", "truncate"]) -> WorkflowStep:
    return replace(fan("fix", "list"), max_items=max_items, overflow=overflow)


def test_item_limit_error_reports_the_step_limit_above_it() -> None:
    result = source("a\n\nb\nc\n")
    assert items_error(capped(2, "error"), result) == (
        "invalid_arguments",
        "output of step 'list' has 3 items, more than the 2 item limit.",
    )


@pytest.mark.parametrize("overflow", ["error", "truncate"])
def test_items_at_and_below_the_step_limit_are_kept_whole(
    overflow: Literal["error", "truncate"],
) -> None:
    result = source("a\n\nb\n")
    assert each_items(capped(2, overflow), result) == EachItems(("a", "b"), 0)
    assert each_items(capped(3, overflow), result) == EachItems(("a", "b"), 0)


def test_truncate_keeps_the_first_items_and_counts_the_rest_after_blanks() -> None:
    result = source("a\n \nb\nc\n\nd\ne\n")
    assert each_items(capped(2, "truncate"), result) == EachItems(("a", "b"), 3)


def test_truncate_applies_to_json_items_after_dropping_null_and_blank() -> None:
    result = source("[]", ["a", None, "", "b", "c"])
    step = replace(fan("fix", "list", split="json"), max_items=2, overflow="truncate")
    assert each_items(step, result) == EachItems(("a", "b"), 1)


def test_each_result_reports_the_dropped_count_after_the_items() -> None:
    instances = (instance(0, child("A")), instance(1, child("B")))
    result = each_result(fan("fix", "list"), ("a", "b"), instances, True, 3)
    assert list(result.payload)[-2:] == ["items", "dropped"]
    assert result.payload["dropped"] == 3
    assert each_result(fan("fix", "list"), ("a", "b"), instances, True, 0).payload["dropped"] == 0


def test_workflow_result_carries_the_dropped_count_into_the_step_entry() -> None:
    outcomes = (outcome("list", child("a\nb\nc")), instance(0, child("A")), instance(1, child("B")))
    items = {"fix": EachItems(("a", "b"), 1)}
    steps = workflow_result("fixes", FIXES, outcomes, 1, None, items)["steps"]
    assert isinstance(steps, list)
    assert steps[1]["dropped"] == 1
