import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from pratfall.adapters.accounting import cost
from pratfall.adapters.native_args import Flag, validate_flags
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
    validate(arguments)
    argv = [*resolved.command, "run", "--format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--variant", resolved.options.effort))
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("OpenCode", arguments, _ALLOWED, _RESERVED)


@dataclass
class _State:
    text_order: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    usage_parts: dict[str, Usage] = field(default_factory=dict)
    cost_parts: dict[str, int | float | None] = field(default_factory=dict)
    completed: bool = False
    provider_error: ResultError | None = None
    protocol_error: ResultError | None = None
    recognized: bool = False


def decode(stdout: str) -> DecodedOutput:
    state = _State()
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            _protocol(state, f"Invalid OpenCode JSONL on line {line_number}: {error.msg}.")
            continue
        except ValueError:
            _protocol(
                state,
                f"Invalid OpenCode JSONL on line {line_number}: numeric value is too large.",
            )
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            _protocol(state, f"Malformed OpenCode event on line {line_number}.")
            continue
        _apply_event(state, event)
    output = "\n".join(state.texts[item] for item in state.text_order)
    usage = _total_usage(state.usage_parts.values()) if state.usage_parts else None
    cost_usd, cost_error = _total_cost(state.cost_parts.values())
    if cost_error is not None:
        state.protocol_error = state.protocol_error or cost_error
    failure = state.provider_error or state.protocol_error
    if failure is not None:
        return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd, error=failure)
    if not state.completed:
        suffix = "" if state.recognized else " (only unknown events were received)"
        return DecodedOutput(
            output=output,
            usage=usage,
            cost_usd=cost_usd,
            error=ResultError(
                "protocol_error", f"OpenCode stream ended without a stop finish{suffix}."
            ),
        )
    return DecodedOutput(output=output, usage=usage, cost_usd=cost_usd)


def _apply_event(state: _State, event: dict[str, object]) -> None:
    event_type = event["type"]
    if event_type == "text":
        state.recognized = True
        _text(state, event)
    elif event_type == "step_finish":
        state.recognized = True
        _finish(state, event)
    elif event_type == "error":
        state.recognized = True
        _error(state, event.get("error"))


def _part(event: dict[str, object], part_type: str) -> dict[str, object] | None:
    part = event.get("part")
    if not isinstance(part, dict) or part.get("type") != part_type:
        return None
    return part


def _text(state: _State, event: dict[str, object]) -> None:
    part = _part(event, "text")
    if part is None:
        _protocol(state, "OpenCode text event is malformed.")
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
        _protocol(state, "OpenCode completed text part is malformed.")
        return
    if part_id not in state.texts:
        state.text_order.append(part_id)
    state.texts[part_id] = text


def _finish(state: _State, event: dict[str, object]) -> None:
    part = _part(event, "step-finish")
    part_id = part.get("id") if part is not None else None
    if part is None or not isinstance(part_id, str):
        _protocol(state, "OpenCode step_finish event is malformed.")
        return
    reason = part.get("reason")
    if not isinstance(reason, str) or reason not in {
        "stop",
        "length",
        "tool-calls",
        "content-filter",
        "error",
        "unknown",
    }:
        _protocol(state, "OpenCode step_finish reason is malformed.")
    elif reason == "stop":
        state.completed = True
    elif reason == "tool-calls":
        state.completed = False
    elif reason in _FAILURE_REASONS:
        state.provider_error = state.provider_error or ResultError(
            "provider_error", f"OpenCode stopped with finish reason {reason!r}."
        )
    part_cost, cost_error = cost(part.get("cost"), "OpenCode step_finish cost")
    state.cost_parts[part_id] = part_cost
    if cost_error is not None:
        state.protocol_error = state.protocol_error or cost_error
    usage = _usage(part.get("tokens"))
    if isinstance(usage, ResultError):
        state.protocol_error = state.protocol_error or usage
    else:
        state.usage_parts[part_id] = usage


def _error(state: _State, value: object) -> None:
    if not isinstance(value, dict):
        _protocol(state, "OpenCode error event is malformed.")
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
        _protocol(state, "OpenCode error event is malformed.")
        return
    diagnostic = name if message is None else message or "OpenCode reported an error."
    state.provider_error = state.provider_error or ResultError("provider_error", diagnostic)


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


def _total_usage(parts: Iterable[Usage]) -> Usage:
    total = Usage(0, 0, 0, 0, 0)
    for part in parts:
        total = Usage(
            input_tokens=(total.input_tokens or 0) + (part.input_tokens or 0),
            cached_input_tokens=(total.cached_input_tokens or 0) + (part.cached_input_tokens or 0),
            cache_write_input_tokens=(total.cache_write_input_tokens or 0)
            + (part.cache_write_input_tokens or 0),
            output_tokens=(total.output_tokens or 0) + (part.output_tokens or 0),
            reasoning_output_tokens=(total.reasoning_output_tokens or 0)
            + (part.reasoning_output_tokens or 0),
        )
    return total


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


def _protocol(state: _State, message: str) -> None:
    state.protocol_error = state.protocol_error or ResultError("protocol_error", message)
