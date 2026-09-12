import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerFailure,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--approve-for-me",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "--oss",
        "--skip-git-repo-check",
        "--strict-config",
    )
} | {
    name: Flag(1, joined=name in {"-i", "-p", "-s"})
    for name in (
        "--add-dir",
        "--disable",
        "--enable",
        "--image",
        "-i",
        "--local-provider",
        "--output-schema",
        "--profile",
        "-p",
        "--sandbox",
        "-s",
        "--thread-source",
    )
}
_RESERVED = {name: Flag(0) for name in ("--json",)} | {
    name: Flag(1, joined=name in {"-c", "-C", "-m", "-o"})
    for name in (
        "--cd",
        "-C",
        "--color",
        "--config",
        "-c",
        "--model",
        "-m",
        "--output-last-message",
        "-o",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "exec", "--json"]
    options = resolved.options
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("-c", f"model_reasoning_effort={json.dumps(options.effort)}"))
    if options.fast is not None:
        service_tier = "priority" if options.fast else "default"
        argv.extend(("-c", f"service_tier={json.dumps(service_tier)}"))
    argv.extend(arguments)
    argv.append("-")
    return Invocation(tuple(argv), prompt)


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Codex", arguments, _ALLOWED, _RESERVED)


@dataclass
class _State:
    answers: list[bytes] = field(default_factory=list)
    current_answer: bytes | None = None
    usage: Usage | None = None
    completed: bool = False
    provider_error: ResultError | None = None
    protocol_error: ResultError | None = None
    current_answer_id: str | None = None
    turn_has_record: bool = False


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Codex", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> str | None:
        return _apply_event(self, event)

    def malformed(self, message: str) -> None:
        _set_protocol(self, message)

    def result(self) -> DecodedOutput:
        state = self.state
        output_parts = state.answers.copy()
        if state.current_answer is not None:
            output_parts.append(state.current_answer)
        output = b"\n".join(output_parts).decode("utf-8")
        failure = state.provider_error or state.protocol_error
        if failure is not None:
            return DecodedOutput(output=output, usage=state.usage, error=failure)
        if not state.completed:
            if self.failed:
                return DecodedOutput(output=output, usage=state.usage)
            message = "Codex stream ended without turn.completed."
            try:
                _set_protocol(self, message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output, usage=state.usage, error=budget_failure.error
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
            return DecodedOutput(
                output=output,
                usage=state.usage,
                error=state.protocol_error,
            )
        return DecodedOutput(output=output, usage=state.usage)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> str | None:
    state = consumer.state
    event_type = event["type"]
    if event_type == "thread.started":
        if not isinstance(event.get("thread_id"), str):
            _set_protocol(consumer, "Codex thread.started event is malformed.")
        return "starting"
    elif event_type == "turn.started":
        if state.current_answer is not None:
            old = len(state.current_answer) + (1 if state.answers else 0)
            consumer.budget.replace_bytes(old, 0)
        if state.current_answer_id is not None:
            consumer.budget.replace_bytes(len(retained_utf8(state.current_answer_id)), 0)
        if state.turn_has_record:
            consumer.budget.remove_record()
        state.current_answer = None
        state.current_answer_id = None
        state.completed = False
        state.turn_has_record = False
        return "working"
    elif event_type in {"item.started", "item.updated", "item.completed"}:
        return _apply_item(consumer, event, event_type)
    elif event_type == "turn.completed":
        decoded_usage = _usage(event.get("usage"))
        if isinstance(decoded_usage, ResultError):
            _set_error(consumer, "protocol_error", decoded_usage.message)
        else:
            _replace_usage(consumer, decoded_usage)
            state.usage = decoded_usage
            if not state.turn_has_record:
                consumer.budget.add_record()
            state.completed = True
            if state.current_answer is not None:
                state.answers.append(state.current_answer)
            state.current_answer = None
            if state.current_answer_id is not None:
                consumer.budget.replace_bytes(len(retained_utf8(state.current_answer_id)), 0)
            state.current_answer_id = None
            state.turn_has_record = False
        return "finishing"
    elif event_type == "turn.failed":
        error = _event_error(event.get("error"), "Codex turn failed.")
        _set_failure(consumer, error)
        return "finishing"
    elif event_type == "error":
        error = _event_error(event, "Codex reported an error.")
        _set_failure(consumer, error)
        return "finishing"
    return None


def _apply_item(consumer: _Consumer, event: dict[str, object], event_type: object) -> str | None:
    state = consumer.state
    item = event.get("item")
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not isinstance(item.get("type"), str)
    ):
        _set_protocol(consumer, f"Codex {event_type} event is malformed.")
        return "working"
    item_id = item["id"]
    if event_type == "item.completed" and item["type"] == "agent_message":
        if state.current_answer_id != item_id:
            if state.current_answer_id is not None:
                consumer.budget.replace_bytes(len(retained_utf8(state.current_answer_id)), 0)
            consumer.budget.add_string(item_id)
            if not state.turn_has_record:
                consumer.budget.add_record()
                state.turn_has_record = True
            state.current_answer_id = item_id
        text = item.get("text")
        if not isinstance(text, str):
            _set_protocol(consumer, "Codex agent_message item is malformed.")
        else:
            encoded = retained_utf8(text)
            old = 0 if state.current_answer is None else len(state.current_answer)
            separator = 1 if state.current_answer is None and state.answers else 0
            consumer.budget.replace_bytes(old, len(encoded) + separator)
            state.completed = False
            state.current_answer = encoded
        return "answering"
    item_type = item["type"]
    if item_type in {"reasoning"}:
        return "reasoning"
    if item_type in {"command_execution", "mcp_tool_call", "web_search"}:
        return "tool"
    return "working"


def _set_failure(consumer: _Consumer, error: ResultError) -> None:
    state = consumer.state
    if error.code == "provider_error":
        if state.provider_error is None:
            consumer.budget.add_string(error.message)
            state.provider_error = error
    else:
        _set_error(consumer, error.code, error.message)


def _set_protocol(consumer: _Consumer, message: str) -> None:
    _set_error(consumer, "protocol_error", message)


def _set_error(consumer: _Consumer, code: str, message: str) -> None:
    if consumer.state.protocol_error is None:
        consumer.budget.add_string(message)
        consumer.state.protocol_error = ResultError(code, message)


def _replace_usage(consumer: _Consumer, usage: Usage) -> None:
    previous = consumer.state.usage
    old = _usage_size(consumer, previous) if previous is not None else 0
    consumer.budget.replace_bytes(old, _usage_size(consumer, usage))


def _usage_size(consumer: _Consumer, usage: Usage) -> int:
    return sum(
        consumer.budget.numeric_size(value)
        for value in (
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.cache_write_input_tokens,
            usage.output_tokens,
            usage.reasoning_output_tokens,
        )
    )


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Codex turn.completed usage is malformed.")
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    optional = ("cache_write_input_tokens", "reasoning_output_tokens")
    values: dict[str, int | None] = {}
    for name in (*required, *optional):
        item = value.get(name)
        if name in optional and item is None:
            values[name] = None
            continue
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            return ResultError("protocol_error", f"Codex usage field {name!r} is malformed.")
        values[name] = item
    return Usage(**values)


def _event_error(value: object, fallback: str) -> ResultError:
    if not isinstance(value, Mapping) or not isinstance(value.get("message"), str):
        return ResultError("protocol_error", "Codex failure event is malformed.")
    return ResultError("provider_error", value["message"] or fallback)
