import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from pratfall.adapters.accounting import cost
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerFailure,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.limits import NUMERIC_BYTES
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--auto",
        "--dangerously-skip-permissions",
        "--print-logs",
        "--pure",
        "--thinking",
        "--yolo",
    )
} | {
    name: Flag(1, joined=name == "-f")
    for name in ("--agent", "--file", "-f", "--log-level", "--title")
}
_RESERVED = {
    name: Flag(0, joined=name in {"-c", "-i"})
    for name in ("--continue", "-c", "--fork", "--interactive", "-i", "--share")
} | {
    name: Flag(1, joined=name in {"-m", "-p", "-s", "-u"})
    for name in (
        "--attach",
        "--command",
        "--dir",
        "--format",
        "-m",
        "--model",
        "-p",
        "--password",
        "--port",
        "-s",
        "--session",
        "-u",
        "--username",
        "--variant",
    )
}
_FAILURE_REASONS = frozenset({"length", "content-filter", "error", "unknown"})
_MESSAGELESS_ERRORS = frozenset({"MessageOutputLengthError"})


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "run", "--format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--variant", resolved.options.effort))
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("OpenCode", resolved.options.native_args or (), _ALLOWED, _RESERVED)


@dataclass
class _State:
    text_order: list[str] = field(default_factory=list)
    texts: dict[str, bytes] = field(default_factory=dict)
    usage_parts: dict[str, Usage] = field(default_factory=dict)
    cost_parts: dict[str, int | float | None] = field(default_factory=dict)
    completed: bool = False
    provider_error: ResultError | None = None
    protocol_error: ResultError | None = None
    recognized: bool = False
    record_ids: set[str] = field(default_factory=set)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("OpenCode", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> str | None:
        return _apply_event(self, event)

    def malformed(self, message: str) -> None:
        _protocol(self, message)

    def result(self) -> DecodedOutput:
        state = self.state
        output = b"\n".join(state.texts[item] for item in state.text_order).decode("utf-8")
        usage, usage_error = _total_usage(state.usage_parts.values())
        if usage_error is not None and not self.failed:
            try:
                _set_protocol_error(self, usage_error)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(output=output, usage=usage, error=budget_failure.error)
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
        cost_usd, cost_error = _total_cost(state.cost_parts.values())
        if cost_error is not None and not self.failed:
            try:
                _set_protocol_error(self, cost_error)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output,
                    usage=usage,
                    cost_usd=cost_usd,
                    error=budget_failure.error,
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
        failure = state.provider_error or state.protocol_error
        if failure is not None:
            return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd, error=failure)
        if not state.completed:
            if self.failed:
                return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd)
            suffix = "" if state.recognized else " (only unknown events were received)"
            message = f"OpenCode stream ended without a stop finish{suffix}."
            try:
                _protocol(self, message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output,
                    usage=usage,
                    cost_usd=cost_usd,
                    error=budget_failure.error,
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
            return DecodedOutput(
                output=output,
                usage=usage,
                cost_usd=cost_usd,
                error=state.protocol_error,
            )
        return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> str | None:
    state = consumer.state
    event_type = event["type"]
    if event_type == "text":
        state.recognized = True
        _text(consumer, event)
        return "answering"
    elif event_type == "step_finish":
        state.recognized = True
        _finish(consumer, event)
        return "finishing"
    elif event_type == "error":
        state.recognized = True
        _error(consumer, event.get("error"))
        return "finishing"
    if event_type in {"tool_call", "tool_result"}:
        return "tool"
    if event_type == "reasoning":
        return "reasoning"
    return None


def _part(event: dict[str, object], part_type: str) -> dict[str, object] | None:
    part = event.get("part")
    if not isinstance(part, dict) or part.get("type") != part_type:
        return None
    return part


def _text(consumer: _Consumer, event: dict[str, object]) -> None:
    state = consumer.state
    part = _part(event, "text")
    if part is None:
        _protocol(consumer, "OpenCode text event is malformed.")
        return
    part_id = part.get("id")
    text = part.get("text")
    time = part.get("time")
    if (
        not isinstance(part_id, str)
        or not isinstance(text, str)
        or not isinstance(time, dict)
        or "end" not in time
    ):
        _protocol(consumer, "OpenCode completed text part is malformed.")
        return
    encoded = retained_utf8(text)
    new_record = part_id not in state.record_ids
    new_text = part_id not in state.texts
    previous = state.texts.get(part_id, b"")
    identifier = retained_utf8(part_id) if new_record else b""
    separator = 1 if new_text and state.text_order else 0
    consumer.budget.replace_state(
        len(previous), len(identifier) + separator + len(encoded), int(new_record)
    )
    if new_record:
        state.record_ids.add(part_id)
    if new_text:
        state.text_order.append(part_id)
    state.texts[part_id] = encoded


def _finish(consumer: _Consumer, event: dict[str, object]) -> None:
    state = consumer.state
    part = _part(event, "step-finish")
    part_id = part.get("id") if part is not None else None
    if part is None or not isinstance(part_id, str):
        _protocol(consumer, "OpenCode step_finish event is malformed.")
        return
    _record(consumer, part_id)
    reason = part.get("reason")
    if not isinstance(reason, str) or reason not in {
        "stop",
        "length",
        "tool-calls",
        "content-filter",
        "error",
        "unknown",
    }:
        _protocol(consumer, "OpenCode step_finish reason is malformed.")
    elif reason == "stop":
        state.completed = True
    elif reason == "tool-calls":
        state.completed = False
    elif reason in _FAILURE_REASONS:
        _provider(consumer, f"OpenCode stopped with finish reason {reason!r}.")
    part_cost, cost_error = cost(part.get("cost"), "OpenCode step_finish cost")
    previous_cost = state.cost_parts.get(part_id)
    consumer.budget.replace_bytes(
        consumer.budget.numeric_size(previous_cost),
        consumer.budget.numeric_size(part_cost),
    )
    state.cost_parts[part_id] = part_cost
    if cost_error is not None:
        _set_protocol_error(consumer, cost_error)
    usage = _usage(part.get("tokens"))
    if isinstance(usage, ResultError):
        _set_protocol_error(consumer, usage)
    else:
        previous_usage = state.usage_parts.get(part_id)
        consumer.budget.replace_bytes(
            _usage_size(consumer, previous_usage), _usage_size(consumer, usage)
        )
        state.usage_parts[part_id] = usage


def _error(consumer: _Consumer, value: object) -> None:
    if not isinstance(value, dict):
        _protocol(consumer, "OpenCode error event is malformed.")
        return
    name = value.get("name")
    data = value.get("data")
    message = data.get("message") if isinstance(data, dict) else None
    if (
        not isinstance(name, str)
        or not isinstance(data, dict)
        or (message is None and name not in _MESSAGELESS_ERRORS)
        or (message is not None and not isinstance(message, str))
    ):
        _protocol(consumer, "OpenCode error event is malformed.")
        return
    diagnostic = name if message is None else message or "OpenCode reported an error."
    _provider(consumer, diagnostic)


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict) or not isinstance(value.get("cache"), dict):
        return ResultError("protocol_error", "OpenCode token statistics are malformed.")
    cache = value["cache"]
    sources = {
        "input": value.get("input"),
        "cached": cache.get("read"),
        "cache_write": cache.get("write"),
        "output": value.get("output"),
        "reasoning": value.get("reasoning"),
    }
    if any(not _token_count(item) for item in sources.values()):
        return ResultError("protocol_error", "OpenCode token statistics are malformed.")
    if "total" in value and not _token_count(value["total"]):
        return ResultError("protocol_error", "OpenCode token statistics are malformed.")
    return Usage(
        input_tokens=sources["input"],
        cached_input_tokens=sources["cached"],
        cache_write_input_tokens=sources["cache_write"],
        output_tokens=sources["output"],
        reasoning_output_tokens=sources["reasoning"],
    )


def _token_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _total_usage(parts: Iterable[Usage]) -> tuple[Usage | None, ResultError | None]:
    total: Usage | None = None
    for part in parts:
        if total is None:
            total = Usage(0, 0, 0, 0, 0)
        values = tuple(
            (left or 0) + (right or 0)
            for left, right in zip(total.__dict__.values(), part.__dict__.values(), strict=True)
        )
        if any(len(str(value)) > NUMERIC_BYTES for value in values):
            return None, ResultError("protocol_error", "OpenCode aggregate usage is malformed.")
        total = Usage(*values)
    return total, None


def _total_cost(
    parts: Iterable[int | float | None],
) -> tuple[int | float | None, ResultError | None]:
    total: int | float | None = None
    unknown = False
    for part in parts:
        if part is None:
            unknown = True
            continue
        try:
            total = part if total is None else total + part
            finite = math.isfinite(total)
        except OverflowError:
            finite = False
        if not finite:
            return None, ResultError("protocol_error", "OpenCode aggregate cost is malformed.")
    return (None if unknown else total), None


def _protocol(consumer: _Consumer, message: str) -> None:
    if consumer.state.protocol_error is None:
        consumer.budget.add_string(message)
        consumer.state.protocol_error = ResultError("protocol_error", message)


def _set_protocol_error(consumer: _Consumer, error: ResultError) -> None:
    _protocol(consumer, error.message)


def _provider(consumer: _Consumer, message: str) -> None:
    if consumer.state.provider_error is None:
        consumer.budget.add_string(message)
        consumer.state.provider_error = ResultError("provider_error", message)


def _record(consumer: _Consumer, part_id: str) -> None:
    if part_id not in consumer.state.record_ids:
        consumer.budget.add_record()
        consumer.budget.add_string(part_id)
        consumer.state.record_ids.add(part_id)


def _usage_size(consumer: _Consumer, usage: Usage | None) -> int:
    if usage is None:
        return 0
    return sum(consumer.budget.numeric_size(value) for value in usage.__dict__.values())
