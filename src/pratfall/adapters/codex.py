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
)
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_TURN_SLOTS = ("answer", "join", "answer_id", "turn")

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


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Codex", resolved.options.native_args or (), _ALLOWED, _RESERVED)


@dataclass
class _State:
    answers: list[bytes] = field(default_factory=list)
    current_answer: bytes | None = None
    usage: Usage | None = None
    completed: bool = False
    provider_error: ResultError | None = None
    current_answer_id: str | None = None


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Codex", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> Activity | None:
        return _apply_event(self, event)

    def result(self) -> DecodedOutput:
        state = self.state
        output_parts = state.answers.copy()
        if state.current_answer is not None:
            output_parts.append(state.current_answer)
        output = b"\n".join(output_parts).decode("utf-8")
        failure = state.provider_error or self.protocol_error
        if failure is not None:
            return DecodedOutput(output=output, usage=state.usage, error=failure)
        if not state.completed:
            if self.failed:
                return DecodedOutput(output=output, usage=state.usage)
            message = "Codex stream ended without turn.completed."
            try:
                self.malformed(message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output, usage=state.usage, error=budget_failure.error
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
            return DecodedOutput(
                output=output,
                usage=state.usage,
                error=self.protocol_error,
            )
        return DecodedOutput(output=output, usage=state.usage)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> Activity | None:
    state = consumer.state
    event_type = event["type"]
    if event_type == "thread.started":
        if not isinstance(event.get("thread_id"), str):
            consumer.malformed("Codex thread.started event is malformed.")
        return "starting"
    elif event_type == "turn.started":
        consumer.retain.release(*_TURN_SLOTS)
        state.current_answer = None
        state.current_answer_id = None
        state.completed = False
        return "working"
    elif event_type in {"item.started", "item.updated", "item.completed"}:
        return _apply_item(consumer, event, event_type)
    elif event_type == "turn.completed":
        decoded_usage = _usage(event.get("usage"))
        if isinstance(decoded_usage, ResultError):
            consumer.malformed(decoded_usage.message)
        else:
            consumer.retain.usage("usage", decoded_usage)
            state.usage = decoded_usage
            consumer.retain.record("turn")
            state.completed = True
            if state.current_answer is not None:
                state.answers.append(state.current_answer)
            state.current_answer = None
            state.current_answer_id = None
            consumer.retain.release("answer_id")
            consumer.retain.commit(*_TURN_SLOTS)
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


def _apply_item(
    consumer: _Consumer, event: dict[str, object], event_type: object
) -> Activity | None:
    state = consumer.state
    item = event.get("item")
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not isinstance(item.get("type"), str)
    ):
        consumer.malformed(f"Codex {event_type} event is malformed.")
        return "working"
    item_id = item["id"]
    if event_type == "item.completed" and item["type"] == "agent_message":
        if state.current_answer_id != item_id:
            consumer.retain.text("answer_id", item_id)
            consumer.retain.record("turn")
            state.current_answer_id = item_id
        text = item.get("text")
        if not isinstance(text, str):
            consumer.malformed("Codex agent_message item is malformed.")
        else:
            # the join byte outlives same-identifier answer replacement
            if state.current_answer is None and state.answers:
                consumer.retain.payload("join", 1)
            state.current_answer = consumer.retain.text("answer", text)
            state.completed = False
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
            consumer.retain.text("provider", error.message)
            state.provider_error = error
    else:
        consumer.malformed(error.message)


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
