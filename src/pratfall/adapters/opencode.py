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
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

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
    recognized: bool = False


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("OpenCode", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> Activity | None:
        return _apply_event(self, event)

    def result(self) -> DecodedOutput:
        state = self.state
        output = b"\n".join(state.texts[item] for item in state.text_order).decode("utf-8")
        usage, usage_error = _total_usage(state.usage_parts.values())
        if usage_error is not None and not self.failed:
            try:
                self.malformed(usage_error.message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(output=output, usage=usage, error=budget_failure.error)
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
        cost_usd, cost_error = _total_cost(state.cost_parts.values())
        if cost_error is not None and not self.failed:
            try:
                self.malformed(cost_error.message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output,
                    usage=usage,
                    cost_usd=cost_usd,
                    error=budget_failure.error,
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
        failure = state.provider_error or self.protocol_error
        if failure is not None:
            return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd, error=failure)
        if not state.completed:
            if self.failed:
                return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd)
            suffix = "" if state.recognized else " (only unknown events were received)"
            message = f"OpenCode stream ended without a stop finish{suffix}."
            try:
                self.malformed(message)
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
                error=self.protocol_error,
            )
        return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> Activity | None:
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
        consumer.malformed("OpenCode text event is malformed.")
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
        consumer.malformed("OpenCode completed text part is malformed.")
        return
    encoded = retained_utf8(text)
    _record(consumer, part_id)
    if part_id not in state.texts:
        if state.text_order:
            consumer.retain.payload(f"join:{part_id}", 1)
        state.text_order.append(part_id)
    consumer.retain.payload(f"text:{part_id}", len(encoded))
    state.texts[part_id] = encoded


def _finish(consumer: _Consumer, event: dict[str, object]) -> None:
    state = consumer.state
    part = _part(event, "step-finish")
    part_id = part.get("id") if part is not None else None
    if part is None or not isinstance(part_id, str):
        consumer.malformed("OpenCode step_finish event is malformed.")
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
        consumer.malformed("OpenCode step_finish reason is malformed.")
    elif reason == "stop":
        state.completed = True
    elif reason == "tool-calls":
        state.completed = False
    elif reason in _FAILURE_REASONS:
        _provider(consumer, f"OpenCode stopped with finish reason {reason!r}.")
    part_cost, cost_error = cost(part.get("cost"), "OpenCode step_finish cost")
    consumer.retain.numbers(f"cost:{part_id}", (part_cost,))
    state.cost_parts[part_id] = part_cost
    if cost_error is not None:
        consumer.malformed(cost_error.message)
    usage = _usage(part.get("tokens"))
    if isinstance(usage, ResultError):
        consumer.malformed(usage.message)
    else:
        consumer.retain.usage(f"usage:{part_id}", usage)
        state.usage_parts[part_id] = usage


def _error(consumer: _Consumer, value: object) -> None:
    if not isinstance(value, dict):
        consumer.malformed("OpenCode error event is malformed.")
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
        consumer.malformed("OpenCode error event is malformed.")
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
        total = _add_usage(total or Usage(), part)
        values = (
            total.input_tokens,
            total.cached_input_tokens,
            total.cache_write_input_tokens,
            total.output_tokens,
            total.reasoning_output_tokens,
        )
        if any(len(str(value)) > NUMERIC_BYTES for value in values):
            return None, ResultError("protocol_error", "OpenCode aggregate usage is malformed.")
    return total, None


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=(left.input_tokens or 0) + (right.input_tokens or 0),
        cached_input_tokens=(left.cached_input_tokens or 0) + (right.cached_input_tokens or 0),
        cache_write_input_tokens=(left.cache_write_input_tokens or 0)
        + (right.cache_write_input_tokens or 0),
        output_tokens=(left.output_tokens or 0) + (right.output_tokens or 0),
        reasoning_output_tokens=(left.reasoning_output_tokens or 0)
        + (right.reasoning_output_tokens or 0),
    )


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


def _provider(consumer: _Consumer, message: str) -> None:
    if consumer.state.provider_error is None:
        consumer.retain.text("provider", message)
        consumer.state.provider_error = ResultError("provider_error", message)


def _record(consumer: _Consumer, part_id: str) -> None:
    consumer.retain.text(f"part:{part_id}", part_id, record=True)
