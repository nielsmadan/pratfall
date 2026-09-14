import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from adapter_helpers import copilot_result
from pratfall.adapters import (
    amp,
    antigravity,
    codex,
    copilot,
    kimi,
    opencode,
    openhands,
    qwen,
    warp,
)
from pratfall.consumer import (
    ByteConsumer,
    ConsumerFactory,
    ConsumerFailure,
    ConsumerLimits,
    JsonlConsumer,
    StateBudget,
    decode_with,
)
from pratfall.models import DecodedOutput, ResultError, Usage

_SEPARATOR = 1
_ENCODING = ResultError("output_encoding", "Agent output contains invalid Unicode text.")
_SURROGATE = "\ud800"


def _stream(*events: object) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def _feed(consumer: ByteConsumer, *events: object) -> None:
    consumer.feed(_stream(*events).encode("ascii"))


def _budget(consumer: ByteConsumer) -> StateBudget:
    if isinstance(consumer, JsonlConsumer):
        return consumer.budget
    assert isinstance(consumer, openhands._Consumer)
    return consumer._budget


def _retained(consumer: ByteConsumer) -> tuple[int, int]:
    budget = _budget(consumer)
    return budget._bytes, budget._records


def _state_limits(state_bytes: int, records: int = 4096) -> ConsumerLimits:
    return ConsumerLimits(state_bytes=state_bytes, records=records)


def _codex_item(item_id: str, text: object) -> dict[str, object]:
    item = {"id": item_id, "type": "agent_message", "text": text}
    return {"type": "item.completed", "item": item}


def _codex_turn(**usage: int) -> dict[str, object]:
    return {"type": "turn.completed", "usage": usage}


def _opencode_text(part_id: str, text: object) -> dict[str, object]:
    part = {"id": part_id, "type": "text", "text": text, "time": {"end": 1}}
    return {"type": "text", "part": part}


def _opencode_tokens(
    tokens_in: int, tokens_out: int, reasoning: int, read: int, write: int
) -> dict[str, object]:
    return {
        "input": tokens_in,
        "output": tokens_out,
        "reasoning": reasoning,
        "cache": {"read": read, "write": write},
    }


def _opencode_step(
    part_id: str, reason: str, tokens: dict[str, object], **extra: object
) -> dict[str, object]:
    part: dict[str, object] = {
        "id": part_id,
        "type": "step-finish",
        "reason": reason,
        "tokens": tokens,
    }
    part.update(extra)
    return {"type": "step_finish", "part": part}


def _amp_assistant(text: object) -> dict[str, object]:
    content = [{"type": "text", "text": text}]
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"content": content}}


def _amp_result(text: object, **usage: int) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "usage": usage,
    }


def _qwen_assistant(text: object, **message: object) -> dict[str, object]:
    content = [{"type": "text", "text": text}]
    return {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"content": content, **message},
    }


def _qwen_result(text: object, **usage: int) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "usage": usage,
    }


def _qwen_failure(message: str, **usage: int) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "error": {"message": message},
        "usage": usage,
    }


def _copilot_message(message_id: str, content: object, **data: object) -> dict[str, object]:
    return {
        "type": "assistant.message",
        "data": {"messageId": message_id, "content": content, **data},
    }


def _copilot_delta(message_id: str, content: str) -> dict[str, object]:
    return {
        "type": "assistant.message_delta",
        "data": {"messageId": message_id, "deltaContent": content},
    }


def _antigravity_result(response: object, **result: object) -> dict[str, object]:
    return {"event": "result", "result": {"response": response, **result}}


def _antigravity_usage(**changes: int) -> dict[str, int]:
    usage = {
        "input_tokens": 1,
        "output_tokens": 2,
        "thinking_tokens": 3,
        "cache_read_tokens": 4,
        "total_tokens": 10,
    }
    usage.update(changes)
    return usage


def _openhands_message(text: object) -> dict[str, object]:
    content = [{"type": "text", "text": text}]
    return {
        "kind": "MessageEvent",
        "source": "agent",
        "llm_message": {"role": "assistant", "content": content},
    }


def _warp_agent(text: object) -> dict[str, object]:
    return {"type": "agent", "text": text}


def _kimi_message(content: object, role: str = "assistant") -> dict[str, object]:
    return {"role": role, "content": content}


def test_codex_retains_identifier_answer_and_usage_for_each_transition() -> None:
    incremental = codex.consumer(_state_limits(1024))

    _feed(incremental, {"type": "thread.started", "thread_id": "t"})
    assert _retained(incremental) == (0, 0)

    _feed(incremental, _codex_item("a1", "hello"))
    assert _retained(incremental) == (len(b"a1") + len(b"hello"), 1)

    _feed(incremental, _codex_item("a1", "hey"))
    assert _retained(incremental) == (len(b"a1") + len(b"hey"), 1)

    usage_one = len(b"1") + len(b"2") + len(b"3")
    _feed(incremental, _codex_turn(input_tokens=1, cached_input_tokens=2, output_tokens=3))
    assert _retained(incremental) == (len(b"hey") + usage_one, 1)

    _feed(incremental, {"type": "turn.started"})
    assert _retained(incremental) == (len(b"hey") + usage_one, 1)

    answered = len(b"hey") + usage_one + len(b"b2") + _SEPARATOR + len(b"world")
    _feed(incremental, _codex_item("b2", "world"))
    assert _retained(incremental) == (answered, 2)

    replaced = len(b"hey") + usage_one + len(b"b2") + _SEPARATOR + len(b"worldly")
    _feed(incremental, _codex_item("b2", "worldly"))
    assert _retained(incremental) == (replaced, 2)

    _feed(incremental, {"type": "turn.started"})
    assert _retained(incremental) == (len(b"hey") + usage_one, 1)

    _feed(incremental, _codex_item("c", "again"))
    carried = len(b"hey") + usage_one + len(b"c") + _SEPARATOR + len(b"again")
    assert _retained(incremental) == (carried, 2)

    usage_two = len(b"100") + len(b"20") + len(b"3")
    _feed(incremental, _codex_turn(input_tokens=100, cached_input_tokens=20, output_tokens=3))
    joined = len(b"hey") + _SEPARATOR + len(b"again")
    assert _retained(incremental) == (joined + usage_two, 2)

    decoded = incremental.finish()
    assert decoded.output == "hey\nagain"
    assert decoded.usage == Usage(100, 20, None, 3, None)
    assert decoded.error is None


def test_codex_replacement_refunds_answer_without_its_join_separator() -> None:
    incremental = codex.consumer(_state_limits(1024))
    _feed(incremental, _codex_item("a", "first"))
    _feed(incremental, _codex_turn(input_tokens=0, cached_input_tokens=0, output_tokens=0))
    usage = len(b"0") * 3
    assert _retained(incremental) == (len(b"first") + usage, 1)

    _feed(incremental, _codex_item("b", "xx"))
    charged = len(b"first") + usage + len(b"b") + _SEPARATOR + len(b"xx")
    assert _retained(incremental) == (charged, 2)

    for text in ("yyyy", "z", "wwwwwwww"):
        _feed(incremental, _codex_item("b", text))
        expected = len(b"first") + usage + len(b"b") + _SEPARATOR + len(text.encode())
        assert _retained(incremental) == (expected, 2)

    _feed(incremental, {"type": "turn.started"})
    assert _retained(incremental) == (len(b"first") + usage, 1)


def test_codex_turn_start_refunds_answer_identifier_and_record_together() -> None:
    incremental = codex.consumer(_state_limits(1024))
    _feed(incremental, _codex_item("identifier", "answer"))
    _feed(incremental, _codex_turn(input_tokens=7, cached_input_tokens=7, output_tokens=7))
    usage = len(b"7") * 3
    assert _retained(incremental) == (len(b"answer") + usage, 1)

    _feed(incremental, _codex_item("second-identifier", "more"))
    charged = len(b"answer") + usage + len(b"second-identifier") + _SEPARATOR + len(b"more")
    assert _retained(incremental) == (charged, 2)

    _feed(incremental, {"type": "turn.started"})
    assert _retained(incremental) == (len(b"answer") + usage, 1)


def test_codex_turn_completion_peaks_above_the_state_it_settles_on() -> None:
    settled = len(b"hi") + len(b"1") + len(b"2") + len(b"3")
    peak = settled + len(b"identifier")
    events = (
        _codex_item("identifier", "hi"),
        _codex_turn(input_tokens=1, cached_input_tokens=2, output_tokens=3),
    )

    accepted = codex.consumer(_state_limits(peak))
    _feed(accepted, *events)
    assert _retained(accepted) == (settled, 1)

    rejected = codex.consumer(_state_limits(peak - 1))
    with pytest.raises(ConsumerFailure) as failure:
        _feed(rejected, *events)
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", f"Agent retained output state exceeded {peak - 1} bytes."
    )


def test_codex_repeated_turn_starts_refund_nothing_that_was_never_charged() -> None:
    incremental = codex.consumer(_state_limits(1024))
    _feed(incremental, _codex_item("a", "answer"))
    _feed(incremental, _codex_turn(input_tokens=0, cached_input_tokens=0, output_tokens=0))
    settled = (len(b"answer") + len(b"0") * 3, 1)
    assert _retained(incremental) == settled

    for _ in range(20):
        _feed(incremental, {"type": "turn.started"})
        assert _retained(incremental) == settled


def test_codex_answer_identifier_change_refunds_previous_identifier() -> None:
    incremental = codex.consumer(_state_limits(1024))
    _feed(incremental, _codex_item("first-id", "text"))
    assert _retained(incremental) == (len(b"first-id") + len(b"text"), 1)

    _feed(incremental, _codex_item("id2", "text"))
    assert _retained(incremental) == (len(b"id2") + len(b"text"), 1)


def test_opencode_retains_identifier_text_cost_and_usage_per_part() -> None:
    incremental = opencode.consumer(_state_limits(1024))

    _feed(incremental, _opencode_text("p1", "hello"))
    assert _retained(incremental) == (len(b"p1") + len(b"hello"), 1)

    _feed(incremental, _opencode_text("p1", "hello there"))
    assert _retained(incremental) == (len(b"p1") + len(b"hello there"), 1)

    texts = len(b"p1") + len(b"hello there") + len(b"p2") + _SEPARATOR + len(b"second")
    _feed(incremental, _opencode_text("p2", "second"))
    assert _retained(incremental) == (texts, 2)

    first_tokens = _opencode_tokens(10, 4, 2, 3, 1)
    first_usage = len(b"10") + len(b"3") + len(b"1") + len(b"4") + len(b"2")
    _feed(incremental, _opencode_step("s1", "tool-calls", first_tokens, cost=0.5))
    step_one = texts + len(b"s1") + len(b"0.5") + first_usage
    assert _retained(incremental) == (step_one, 3)

    second_tokens = _opencode_tokens(100, 40, 0, 0, 0)
    second_usage = len(b"100") + len(b"0") + len(b"0") + len(b"40") + len(b"0")
    _feed(incremental, _opencode_step("s1", "stop", second_tokens, cost=0.25))
    step_two = texts + len(b"s1") + len(b"0.25") + second_usage
    assert _retained(incremental) == (step_two, 3)

    third_tokens = _opencode_tokens(1, 1, 1, 1, 1)
    third_usage = len(b"1") * 5
    _feed(incremental, _opencode_step("s2", "stop", third_tokens))
    step_three = step_two + len(b"s2") + third_usage
    assert _retained(incremental) == (step_three, 4)

    decoded = incremental.finish()
    assert decoded.output == "hello there\nsecond"
    assert decoded.usage == Usage(101, 1, 1, 41, 1)
    assert decoded.cost_usd is None
    assert decoded.error is None


def test_opencode_second_text_part_charges_exactly_one_join_separator() -> None:
    events = (_opencode_text("a", "one"), _opencode_text("b", "two"))
    joined = len(b"a") + len(b"one") + len(b"b") + _SEPARATOR + len(b"two")

    incremental = opencode.consumer(_state_limits(1024))
    _feed(incremental, *events)
    assert _retained(incremental) == (joined, 2)

    accepted = opencode.consumer(_state_limits(joined))
    _feed(accepted, *events)
    assert _retained(accepted) == (joined, 2)

    rejected = opencode.consumer(_state_limits(joined - 1))
    with pytest.raises(ConsumerFailure) as failure:
        _feed(rejected, *events)
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", f"Agent retained output state exceeded {joined - 1} bytes."
    )


def test_opencode_step_replacement_refunds_cost_and_usage_each_time() -> None:
    incremental = opencode.consumer(_state_limits(1024))
    _feed(incremental, _opencode_text("t", "answer"))
    base = len(b"t") + len(b"answer")
    assert _retained(incremental) == (base, 1)

    costs = (0.5, 12.25, 0, 1000.125, 3)
    counts = (1, 22, 333, 4444, 5)
    for part_cost, count in zip(costs, counts, strict=True):
        tokens = _opencode_tokens(count, count, count, count, count)
        _feed(incremental, _opencode_step("s", "tool-calls", tokens, cost=part_cost))
        usage = len(str(count).encode()) * 5
        expected = base + len(b"s") + len(str(part_cost).encode()) + usage
        assert _retained(incremental) == (expected, 2)

    decoded = incremental.finish()
    assert decoded.cost_usd == 3
    assert decoded.usage == Usage(5, 5, 5, 5, 5)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_opencode_unknown_part_cost_refunds_to_zero_and_clears_the_total() -> None:
    incremental = opencode.consumer(_state_limits(1024))
    _feed(incremental, _opencode_text("t", "answer"))
    base = len(b"t") + len(b"answer")

    tokens = _opencode_tokens(0, 0, 0, 0, 0)
    usage = len(b"0") * 5
    _feed(incremental, _opencode_step("s", "stop", tokens, cost=7.5))
    assert _retained(incremental) == (base + len(b"s") + len(b"7.5") + usage, 2)

    _feed(incremental, _opencode_step("s", "stop", tokens))
    assert _retained(incremental) == (base + len(b"s") + usage, 2)

    decoded = incremental.finish()
    assert decoded.cost_usd is None
    assert decoded.error is None


def test_opencode_text_after_step_finish_reuses_the_charged_identifier() -> None:
    incremental = opencode.consumer(_state_limits(1024))
    tokens = _opencode_tokens(0, 0, 0, 0, 0)
    usage = len(b"0") * 5
    _feed(incremental, _opencode_step("shared", "stop", tokens, cost=0))
    assert _retained(incremental) == (len(b"shared") + len(b"0") + usage, 1)

    _feed(incremental, _opencode_text("shared", "answer"))
    assert _retained(incremental) == (len(b"shared") + len(b"0") + usage + len(b"answer"), 1)


def test_amp_retains_one_answer_and_one_usage_snapshot() -> None:
    incremental = amp.consumer(_state_limits(1024))

    _feed(incremental, {"type": "system", "subtype": "init"})
    assert _retained(incremental) == (0, 0)

    _feed(incremental, _amp_assistant("hi"))
    assert _retained(incremental) == (len(b"hi"), 1)

    _feed(incremental, _amp_assistant("hello"))
    assert _retained(incremental) == (len(b"hello"), 1)

    usage = len(b"10") + len(b"3") + len(b"1") + len(b"4")
    _feed(
        incremental,
        _amp_result(
            "final",
            input_tokens=10,
            output_tokens=4,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=1,
        ),
    )
    assert _retained(incremental) == (len(b"final") + usage, 2)

    decoded = incremental.finish()
    assert decoded.output == "final"
    assert decoded.usage == Usage(10, 3, 1, 4, None)
    assert decoded.error is None


def test_qwen_retains_model_answer_and_replaced_result_snapshot() -> None:
    incremental = qwen.consumer(_state_limits(1024))

    _feed(incremental, {"type": "system", "subtype": "init"})
    assert _retained(incremental) == (0, 0)

    _feed(incremental, _qwen_assistant("draft", model="qw-1"))
    assert _retained(incremental) == (len(b"qw-1") + len(b"draft"), 2)

    _feed(incremental, _qwen_assistant("drafted", model="qw-1"))
    assert _retained(incremental) == (len(b"qw-1") + len(b"drafted"), 2)

    usage = len(b"12") + len(b"4") + len(b"3")
    _feed(
        incremental,
        _qwen_result("final", input_tokens=12, output_tokens=3, cache_read_input_tokens=4),
    )
    assert _retained(incremental) == (len(b"qw-1") + len(b"final") + usage, 3)

    failure_usage = len(b"1") + len(b"1")
    _feed(incremental, _qwen_failure("boom", input_tokens=1, output_tokens=1))
    replaced = len(b"qw-1") + len(b"final") + len(b"boom") + failure_usage
    assert _retained(incremental) == (replaced, 3)

    decoded = incremental.finish()
    assert decoded.output == "final"
    assert decoded.usage == Usage(1, None, None, 1, None)
    assert decoded.reported_models == ("qw-1",)
    assert decoded.error == ResultError("provider_error", "boom")


def test_qwen_repeated_failures_refund_the_previous_result_snapshot() -> None:
    incremental = qwen.consumer(_state_limits(1024))
    _feed(incremental, _qwen_assistant("answer"))
    assert _retained(incremental) == (len(b"answer"), 1)

    for message, count in (("short", 1), ("a much longer failure", 22), ("mid", 333)):
        _feed(incremental, _qwen_failure(message, input_tokens=count, output_tokens=count))
        usage = len(str(count).encode()) * 2
        assert _retained(incremental) == (len(b"answer") + len(message.encode()) + usage, 2)


def test_warp_charges_every_answer_and_never_refunds() -> None:
    incremental = warp.consumer(_state_limits(1024))

    _feed(incremental, _warp_agent("alpha"))
    assert _retained(incremental) == (len(b"alpha"), 1)

    _feed(incremental, _warp_agent("beta"))
    assert _retained(incremental) == (len(b"alpha") + _SEPARATOR + len(b"beta"), 2)

    _feed(incremental, {"type": "tool_call"})
    assert _retained(incremental) == (len(b"alpha") + _SEPARATOR + len(b"beta"), 2)

    _feed(incremental, _warp_agent(""))
    joined = len(b"alpha") + _SEPARATOR + len(b"beta") + _SEPARATOR
    assert _retained(incremental) == (joined, 3)

    decoded = incremental.finish()
    assert decoded.output == "alpha\nbeta\n"
    assert decoded.error is None


def test_kimi_replaces_the_answer_and_never_charges_a_record() -> None:
    incremental = kimi.consumer(_state_limits(1024))

    _feed(incremental, _kimi_message("first"))
    assert _retained(incremental) == (len(b"first"), 0)

    _feed(incremental, _kimi_message("a much longer answer"))
    assert _retained(incremental) == (len(b"a much longer answer"), 0)

    _feed(incremental, _kimi_message("ok"))
    assert _retained(incremental) == (len(b"ok"), 0)

    _feed(incremental, _kimi_message("x", role="user"))
    diagnostic = len(b"Malformed Kimi final assistant message.")
    assert _retained(incremental) == (len(b"ok") + diagnostic, 0)


def test_openhands_charges_one_terminal_answer_and_replaces_diagnostics() -> None:
    incremental = openhands.consumer(_state_limits(1024))

    _feed(incremental, {"kind": "SystemPromptEvent"})
    assert _retained(incremental) == (0, 0)

    _feed(incremental, _openhands_message("done"))
    assert _retained(incremental) == (len(b"done"), 1)

    _feed(incremental, {"kind": "ConversationErrorEvent", "code": "E", "detail": "blew up"})
    assert _retained(incremental) == (len(b"done") + len(b"blew up"), 1)

    _feed(incremental, {"kind": "ConversationErrorEvent", "code": "E", "detail": "later"})
    assert _retained(incremental) == (len(b"done") + len(b"blew up"), 1)


def test_openhands_provider_error_refunds_the_retained_protocol_diagnostic() -> None:
    incremental = openhands.consumer(_state_limits(1024))

    _feed(incremental, {"kind": "UnknownKind"})
    assert _retained(incremental) == (len(b"Unknown OpenHands event kind."), 0)

    _feed(incremental, {"kind": "ConversationErrorEvent", "code": "E", "detail": "boom"})
    assert _retained(incremental) == (len(b"boom"), 0)


def test_antigravity_charges_init_result_answer_and_usage_fields() -> None:
    incremental = antigravity.consumer(_state_limits(1024))

    _feed(incremental, {"event": "init", "init": {}})
    assert _retained(incremental) == (0, 1)

    _feed(incremental, {"event": "step_update", "step_update": {}})
    assert _retained(incremental) == (0, 1)

    usage = len(b"1") + len(b"4") + len(b"2") + len(b"3")
    _feed(
        incremental,
        _antigravity_result("answer", status="SUCCESS", usage=_antigravity_usage()),
    )
    assert _retained(incremental) == (len(b"answer") + usage, 2)


def test_antigravity_late_provider_error_refunds_the_retained_result_error() -> None:
    incremental = antigravity.consumer(_state_limits(1024))
    _feed(incremental, {"event": "init", "init": {}})

    nonterminal = len(b"Antigravity result has nonterminal status 'WAITING'.")
    _feed(incremental, _antigravity_result("partial", status="WAITING"))
    assert _retained(incremental) == (len(b"partial") + nonterminal, 2)

    malformed = len(b"Antigravity result event is malformed.")
    _feed(incremental, _antigravity_result("", status="ERROR", error="failed hard"))
    replaced = len(b"partial") + malformed + len(b"failed hard")
    assert _retained(incremental) == (replaced, 2)

    _feed(incremental, _antigravity_result("", status="ERROR", error="second failure"))
    assert _retained(incremental) == (replaced, 2)


def test_copilot_charges_identifier_delta_growth_model_and_terminal_record() -> None:
    incremental = copilot.consumer(_state_limits(1024))

    _feed(incremental, _copilot_delta("m1", "Hel"))
    assert _retained(incremental) == (len(b"m1") + len(b"Hel"), 1)

    _feed(incremental, _copilot_delta("m1", "lo"))
    assert _retained(incremental) == (len(b"m1") + len(b"Hello"), 1)

    _feed(incremental, _copilot_message("m1", "Hello!", model="gpt-x"))
    complete = len(b"m1") + len(b"Hello!") + len(b"gpt-x")
    assert _retained(incremental) == (complete, 2)

    _feed(incremental, _copilot_delta("m1", "ignored"))
    assert _retained(incremental) == (complete, 2)

    _feed(incremental, _copilot_message("m2", "second", model="gpt-x"))
    second = complete + len(b"m2") + _SEPARATOR + len(b"second")
    assert _retained(incremental) == (second, 3)

    _feed(incremental, copilot_result())
    assert _retained(incremental) == (second, 4)

    decoded = incremental.finish()
    assert decoded.output == "Hello!\nsecond"
    assert decoded.reported_models == ("gpt-x",)
    assert decoded.error is None


@dataclass(frozen=True)
class _Canonical:
    name: str
    factory: ConsumerFactory
    stream: str
    state_bytes: int
    records: int
    expected: DecodedOutput
    peak_bytes: int = 0

    @property
    def ceiling(self) -> int:
        return self.peak_bytes or self.state_bytes


_AMP = _Canonical(
    "amp",
    amp.consumer,
    _stream(_amp_result("final", input_tokens=1, output_tokens=2)),
    len(b"final") + len(b"1") + len(b"2"),
    2,
    DecodedOutput(output="final", usage=Usage(1, None, None, 2, None)),
)
_ANTIGRAVITY = _Canonical(
    "antigravity",
    antigravity.consumer,
    _stream(
        {"event": "init", "init": {}},
        _antigravity_result("ok", status="SUCCESS", usage=_antigravity_usage()),
    ),
    len(b"ok") + len(b"1") + len(b"4") + len(b"2") + len(b"3"),
    2,
    DecodedOutput(output="ok", usage=Usage(1, 4, None, 2, 3)),
)
_CODEX = _Canonical(
    "codex",
    codex.consumer,
    _stream(
        _codex_item("a", "hi"),
        _codex_turn(input_tokens=1, cached_input_tokens=2, output_tokens=3),
    ),
    len(b"hi") + len(b"1") + len(b"2") + len(b"3"),
    1,
    DecodedOutput(output="hi", usage=Usage(1, 2, None, 3, None)),
    peak_bytes=len(b"a") + len(b"hi") + len(b"1") + len(b"2") + len(b"3"),
)
_COPILOT = _Canonical(
    "copilot",
    copilot.consumer,
    _stream(_copilot_message("m", "hi", model="g"), copilot_result()),
    len(b"m") + len(b"hi") + len(b"g"),
    3,
    DecodedOutput(output="hi", reported_models=("g",)),
)
_KIMI = _Canonical(
    "kimi",
    kimi.consumer,
    _stream(_kimi_message("hi")),
    len(b"hi"),
    0,
    DecodedOutput(output="hi"),
)
_OPENCODE = _Canonical(
    "opencode",
    opencode.consumer,
    _stream(
        _opencode_text("p", "hi"),
        _opencode_step("p", "stop", _opencode_tokens(1, 2, 3, 4, 5), cost=0),
    ),
    len(b"p") + len(b"hi") + len(b"0") + len(b"1") * 5,
    1,
    DecodedOutput(output="hi", usage=Usage(1, 4, 5, 2, 3), cost_usd=0),
)
_OPENHANDS = _Canonical(
    "openhands",
    openhands.consumer,
    _stream(_openhands_message("hi")),
    len(b"hi"),
    1,
    DecodedOutput(output="hi"),
)
_QWEN = _Canonical(
    "qwen",
    qwen.consumer,
    _stream(_qwen_result("hi", input_tokens=1, output_tokens=2)),
    len(b"hi") + len(b"1") + len(b"2"),
    2,
    DecodedOutput(output="hi", usage=Usage(1, None, None, 2, None)),
)
_WARP = _Canonical(
    "warp",
    warp.consumer,
    _stream(_warp_agent("hi")),
    len(b"hi"),
    1,
    DecodedOutput(output="hi"),
)

_CANONICAL = (
    _AMP,
    _ANTIGRAVITY,
    _CODEX,
    _COPILOT,
    _KIMI,
    _OPENCODE,
    _OPENHANDS,
    _QWEN,
    _WARP,
)
_WITH_RECORDS = tuple(case for case in _CANONICAL if case.records > 0)


def _ids(cases: tuple[_Canonical, ...]) -> list[str]:
    return [case.name for case in cases]


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_retained_state_exactly_on_the_byte_limit_is_accepted(case: _Canonical) -> None:
    incremental = case.factory(ConsumerLimits(state_bytes=case.ceiling, records=case.records))
    incremental.feed(case.stream.encode("ascii"))
    assert _retained(incremental) == (case.state_bytes, case.records)
    assert incremental.finish() == case.expected


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_retained_state_one_byte_over_the_limit_is_rejected(case: _Canonical) -> None:
    limits = ConsumerLimits(state_bytes=case.ceiling - 1, records=case.records)
    incremental = case.factory(limits)
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(case.stream.encode("ascii"))
        incremental.finish()
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded",
        f"Agent retained output state exceeded {case.ceiling - 1} bytes.",
    )


@pytest.mark.parametrize("case", _WITH_RECORDS, ids=_ids(_WITH_RECORDS))
def test_retained_records_one_below_the_limit_is_rejected(case: _Canonical) -> None:
    limits = ConsumerLimits(state_bytes=case.ceiling, records=case.records - 1)
    incremental = case.factory(limits)
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(case.stream.encode("ascii"))
        incremental.finish()
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded",
        f"Agent retained output records exceeded {case.records - 1}.",
    )


def test_codex_partial_answer_and_usage_survive_a_missing_turn_diagnostic() -> None:
    retained = len(b"partial") + len(b"1") + len(b"2") + len(b"3")
    ceiling = retained + len(b"a")
    incremental = codex.consumer(_state_limits(ceiling))
    _feed(
        incremental,
        _codex_item("a", "partial"),
        _codex_turn(input_tokens=1, cached_input_tokens=2, output_tokens=3),
        {"type": "turn.started"},
    )
    assert _retained(incremental) == (retained, 1)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    assert failure.value.error == ResultError(
        "stdout_limit_exceeded", f"Agent retained output state exceeded {ceiling} bytes."
    )
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.usage == Usage(1, 2, None, 3, None)
    assert decoded.cost_usd is None
    assert decoded.error == failure.value.error


def test_copilot_partial_answer_and_models_survive_a_missing_result_diagnostic() -> None:
    retained = len(b"m") + len(b"partial") + len(b"gm")
    incremental = copilot.consumer(_state_limits(retained))
    _feed(incremental, _copilot_message("m", "partial", model="gm"))
    assert _retained(incremental) == (retained, 2)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.reported_models == ("gm",)
    assert decoded.usage is None
    assert decoded.cost_usd is None
    assert decoded.error == failure.value.error


def test_opencode_partial_answer_survives_an_aggregate_usage_diagnostic() -> None:
    huge = 10**128 - 1
    tokens = _opencode_tokens(huge, 0, 0, 0, 0)
    part_usage = len(str(huge).encode()) + len(b"0") * 4
    retained = (
        len(b"t")
        + len(b"partial")
        + (len(b"a") + len(b"0") + part_usage)
        + (len(b"b") + len(b"0") + part_usage)
    )
    incremental = opencode.consumer(_state_limits(retained))
    _feed(
        incremental,
        _opencode_text("t", "partial"),
        _opencode_step("a", "stop", tokens, cost=0),
        _opencode_step("b", "stop", tokens, cost=0),
    )
    assert _retained(incremental) == (retained, 3)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.usage is None
    assert decoded.cost_usd is None
    assert decoded.error == failure.value.error


def test_opencode_partial_answer_and_usage_survive_an_aggregate_cost_diagnostic() -> None:
    tokens = _opencode_tokens(1, 1, 1, 1, 1)
    part = len(b"1e+308") + len(b"1") * 5
    retained = len(b"t") + len(b"partial") + (len(b"a") + part) + (len(b"b") + part)
    incremental = opencode.consumer(_state_limits(retained))
    _feed(
        incremental,
        _opencode_text("t", "partial"),
        _opencode_step("a", "stop", tokens, cost=1e308),
        _opencode_step("b", "stop", tokens, cost=1e308),
    )
    assert _retained(incremental) == (retained, 3)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.usage == Usage(2, 2, 2, 2, 2)
    assert decoded.cost_usd is None
    assert decoded.error == failure.value.error


def test_opencode_partial_answer_usage_and_cost_survive_a_missing_stop_diagnostic() -> None:
    tokens = _opencode_tokens(1, 2, 3, 4, 5)
    retained = len(b"t") + len(b"partial") + len(b"a") + len(b"0.5") + len(b"1") * 5
    incremental = opencode.consumer(_state_limits(retained))
    _feed(
        incremental,
        _opencode_text("t", "partial"),
        _opencode_step("a", "tool-calls", tokens, cost=0.5),
    )
    assert _retained(incremental) == (retained, 2)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.usage == Usage(1, 4, 5, 2, 3)
    assert decoded.cost_usd == 0.5
    assert decoded.error == failure.value.error


def test_openhands_partial_answer_survives_a_duplicate_terminal_diagnostic() -> None:
    incremental = openhands.consumer(_state_limits(len(b"partial")))
    _feed(incremental, _openhands_message("partial"))
    assert _retained(incremental) == (len(b"partial"), 1)

    with pytest.raises(ConsumerFailure) as feeding:
        _feed(incremental, _openhands_message("x"))
    assert feeding.value.error.code == "stdout_limit_exceeded"

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    decoded = failure.value.decoded
    assert decoded is not None
    assert decoded.output == "partial"
    assert decoded.usage is None
    assert decoded.cost_usd is None
    assert decoded.error is None


@pytest.mark.parametrize(
    ("factory", "accepted", "rejected", "expected"),
    [
        (
            amp.consumer,
            _amp_assistant("partial"),
            _amp_assistant("muchlongeranswer"),
            DecodedOutput(output="partial"),
        ),
        (
            kimi.consumer,
            _kimi_message("partial"),
            _kimi_message("muchlongeranswer"),
            DecodedOutput(output="partial"),
        ),
        (
            qwen.consumer,
            _qwen_assistant("partial"),
            _qwen_assistant("muchlongeranswer"),
            DecodedOutput(output="partial"),
        ),
        (
            warp.consumer,
            _warp_agent("partial"),
            _warp_agent("x"),
            DecodedOutput(output="partial"),
        ),
    ],
    ids=["amp", "kimi", "qwen", "warp"],
)
def test_shared_finalization_carries_the_answer_retained_before_the_failure(
    factory: ConsumerFactory, accepted: object, rejected: object, expected: DecodedOutput
) -> None:
    incremental = factory(_state_limits(len(b"partial")))
    _feed(incremental, accepted)
    assert _retained(incremental)[0] == len(b"partial")

    with pytest.raises(ConsumerFailure) as feeding:
        _feed(incremental, rejected)
    assert feeding.value.error.code == "stdout_limit_exceeded"

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    assert failure.value.error == feeding.value.error
    assert failure.value.decoded == expected


def test_antigravity_finalization_carries_the_answer_and_usage_before_the_failure() -> None:
    retained = len(b"partial") + len(b"1") + len(b"4") + len(b"2") + len(b"3")
    incremental = antigravity.consumer(_state_limits(retained))
    _feed(
        incremental,
        {"event": "init", "init": {}},
        _antigravity_result("partial", status="SUCCESS", usage=_antigravity_usage()),
    )
    assert _retained(incremental) == (retained, 2)

    with pytest.raises(ConsumerFailure) as feeding:
        _feed(incremental, _antigravity_result("again", status="SUCCESS"))
    assert feeding.value.error.code == "stdout_limit_exceeded"

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    assert failure.value.decoded == DecodedOutput(output="partial", usage=Usage(1, 4, None, 2, 3))


def test_trailing_record_failure_during_finish_still_carries_the_prior_answer() -> None:
    incremental = kimi.consumer(_state_limits(len(b"partial")))
    payload = _stream(_kimi_message("partial")) + json.dumps(_kimi_message("muchlonger"))
    incremental.feed(payload.encode("ascii"))
    assert _retained(incremental) == (len(b"partial"), 0)

    with pytest.raises(ConsumerFailure) as failure:
        incremental.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"
    assert failure.value.decoded == DecodedOutput(output="partial")


@pytest.mark.parametrize(
    ("factory", "events"),
    [
        (amp.consumer, (_amp_result(_SURROGATE, input_tokens=1, output_tokens=2),)),
        (
            antigravity.consumer,
            (
                {"event": "init", "init": {}},
                _antigravity_result(_SURROGATE, status="SUCCESS", usage=_antigravity_usage()),
            ),
        ),
        (codex.consumer, (_codex_item("a", _SURROGATE),)),
        (copilot.consumer, (_copilot_message("m", _SURROGATE),)),
        (kimi.consumer, (_kimi_message(_SURROGATE),)),
        (opencode.consumer, (_opencode_text("p", _SURROGATE),)),
        (openhands.consumer, (_openhands_message(_SURROGATE),)),
        (qwen.consumer, (_qwen_assistant(_SURROGATE),)),
        (warp.consumer, (_warp_agent(_SURROGATE),)),
    ],
    ids=[
        "amp",
        "antigravity",
        "codex",
        "copilot",
        "kimi",
        "opencode",
        "openhands",
        "qwen",
        "warp",
    ],
)
def test_retained_answer_with_an_unpaired_surrogate_reports_output_encoding(
    factory: ConsumerFactory, events: tuple[object, ...]
) -> None:
    incremental = factory(_state_limits(1024))
    with pytest.raises(ConsumerFailure) as failure:
        _feed(incremental, *events)
    assert failure.value.error == _ENCODING


@pytest.mark.parametrize(
    ("factory", "events"),
    [
        (codex.consumer, (_codex_item(_SURROGATE, "text"),)),
        (copilot.consumer, (_copilot_message(_SURROGATE, "text"),)),
        (opencode.consumer, (_opencode_text(_SURROGATE, "text"),)),
    ],
    ids=["codex", "copilot", "opencode"],
)
def test_retained_identifier_with_an_unpaired_surrogate_reports_output_encoding(
    factory: ConsumerFactory, events: tuple[object, ...]
) -> None:
    incremental = factory(_state_limits(1024))
    with pytest.raises(ConsumerFailure) as failure:
        _feed(incremental, *events)
    assert failure.value.error == _ENCODING


def test_qwen_reported_model_with_an_unpaired_surrogate_reports_protocol_error() -> None:
    incremental = qwen.consumer(_state_limits(1024))
    _feed(
        incremental,
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {
                "content": [],
                "model": _SURROGATE,
            },
        },
    )
    message = "Qwen assistant model is malformed."
    assert _retained(incremental) == (len(message.encode()), 0)
    decoded = incremental.finish()
    assert decoded.error == ResultError("protocol_error", message)


def _chunks(data: bytes, size: int) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def _drive(factory: ConsumerFactory, data: bytes, size: int) -> tuple[DecodedOutput, int, int]:
    incremental = factory()
    for chunk in _chunks(data, size):
        incremental.feed(chunk)
    decoded = incremental.finish()
    retained = _retained(incremental)
    return decoded, retained[0], retained[1]


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_chunk_boundaries_do_not_change_the_decoded_result_or_the_budget(
    case: _Canonical,
) -> None:
    data = case.stream.encode("ascii")
    results = [_drive(case.factory, data, size) for size in (1, 7, 64 * 1024, len(data))]
    assert results == [(case.expected, case.state_bytes, case.records)] * len(results)


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_incremental_feeding_matches_the_whole_document_convenience_decoder(
    case: _Canonical,
) -> None:
    assert decode_with(case.factory, case.stream) == case.expected


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_a_missing_trailing_newline_does_not_change_the_budget(case: _Canonical) -> None:
    trimmed = case.stream.rstrip("\n").encode("ascii")
    decoded, state_bytes, records = _drive(case.factory, trimmed, len(trimmed))
    assert (decoded, state_bytes, records) == (case.expected, case.state_bytes, case.records)


@pytest.mark.parametrize("case", _CANONICAL, ids=_ids(_CANONICAL))
def test_every_consumer_starts_with_an_empty_budget(case: _Canonical) -> None:
    assert _retained(case.factory(_state_limits(1024))) == (0, 0)
