import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pratfall.adapters.native_args import Flag, validate_flags
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
    answers: list[str] = field(default_factory=list)
    current_answer: str | None = None
    usage: Usage | None = None
    completed: bool = False
    provider_error: ResultError | None = None
    protocol_error: ResultError | None = None


def decode(stdout: str) -> DecodedOutput:
    state = _State()
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            state.protocol_error = state.protocol_error or ResultError(
                "protocol_error", f"Invalid Codex JSONL on line {line_number}: {error.msg}."
            )
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            state.protocol_error = state.protocol_error or ResultError(
                "protocol_error", f"Malformed Codex event on line {line_number}."
            )
            continue
        _apply_event(state, event)
    failure = state.provider_error or state.protocol_error
    output_parts = state.answers.copy()
    if failure is not None and state.current_answer is not None:
        output_parts.append(state.current_answer)
    output = "\n".join(output_parts)
    if failure is not None:
        return DecodedOutput(output=output, usage=state.usage, error=failure)
    if not state.completed:
        return DecodedOutput(
            output=output,
            usage=state.usage,
            error=ResultError("protocol_error", "Codex stream ended without turn.completed."),
        )
    return DecodedOutput(output=output, usage=state.usage)


def _apply_event(state: _State, event: dict[str, object]) -> None:
    event_type = event["type"]
    if event_type == "thread.started":
        if not isinstance(event.get("thread_id"), str):
            _set_protocol(state, "Codex thread.started event is malformed.")
    elif event_type == "turn.started":
        state.current_answer = None
        state.completed = False
    elif event_type in {"item.started", "item.updated", "item.completed"}:
        _apply_item(state, event, event_type)
    elif event_type == "turn.completed":
        decoded_usage = _usage(event.get("usage"))
        if isinstance(decoded_usage, ResultError):
            state.protocol_error = state.protocol_error or decoded_usage
        else:
            state.usage = decoded_usage
            state.completed = True
            if state.current_answer is not None:
                state.answers.append(state.current_answer)
            state.current_answer = None
    elif event_type == "turn.failed":
        error = _event_error(event.get("error"), "Codex turn failed.")
        _set_failure(state, error)
    elif event_type == "error":
        error = _event_error(event, "Codex reported an error.")
        _set_failure(state, error)


def _apply_item(state: _State, event: dict[str, object], event_type: object) -> None:
    item = event.get("item")
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not isinstance(item.get("type"), str)
    ):
        _set_protocol(state, f"Codex {event_type} event is malformed.")
        return
    if event_type == "item.completed" and item["type"] == "agent_message":
        text = item.get("text")
        if not isinstance(text, str):
            _set_protocol(state, "Codex agent_message item is malformed.")
        else:
            state.current_answer = text


def _set_failure(state: _State, error: ResultError) -> None:
    if error.code == "provider_error":
        state.provider_error = state.provider_error or error
    else:
        state.protocol_error = state.protocol_error or error


def _set_protocol(state: _State, message: str) -> None:
    state.protocol_error = state.protocol_error or ResultError("protocol_error", message)


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
