import json
from dataclasses import dataclass

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
        "--new-project",
        "--sandbox",
    )
} | {name: Flag(1) for name in ("--add-dir", "--agent", "--log-file", "--mode", "--project")}
_RESERVED = {
    name: Flag(0, joined=name in {"-c", "-i", "-p"})
    for name in ("-c", "--continue", "-i", "--print", "-p")
} | {
    name: Flag(1)
    for name in (
        "--conversation",
        "--effort",
        "--input-format",
        "--json-schema",
        "--model",
        "--output-format",
        "--print-timeout",
        "--prompt",
        "--prompt-interactive",
    )
}


@dataclass
class _State:
    initialized: bool = False
    result: dict[str, object] | None = None
    protocol_error: ResultError | None = None


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "--input-format", "stream-json", "--output-format", "stream-json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--effort", resolved.options.effort))
    argv.extend(arguments)
    event = {"event": "user", "message": {"content": prompt.decode("utf-8")}}
    return Invocation(tuple(argv), (json.dumps(event, ensure_ascii=False) + "\n").encode())


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Antigravity", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    state = _State()
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            state.protocol_error = state.protocol_error or ResultError(
                "protocol_error", f"Invalid Antigravity JSONL on line {line_number}: {error.msg}."
            )
            continue
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            state.protocol_error = state.protocol_error or ResultError(
                "protocol_error", f"Malformed Antigravity event on line {line_number}."
            )
            continue
        _apply_event(state, event)
    if state.result is None:
        return DecodedOutput(
            error=state.protocol_error
            or ResultError("protocol_error", "Antigravity stream ended without a result event.")
        )
    decoded = _decode_result(state.result)
    if decoded.error is not None and decoded.error.code == "provider_error":
        return decoded
    if state.protocol_error is not None:
        return DecodedOutput(output=decoded.output, usage=decoded.usage, error=state.protocol_error)
    if not state.initialized:
        return DecodedOutput(
            output=decoded.output,
            usage=decoded.usage,
            error=ResultError("protocol_error", "Antigravity stream is missing its init event."),
        )
    return decoded


def _apply_event(state: _State, event: dict[str, object]) -> None:
    event_type = event["event"]
    if event_type == "init":
        if state.initialized or state.result is not None or not isinstance(event.get("init"), dict):
            _record_protocol(state, "Antigravity init event is malformed.")
        state.initialized = True
    elif event_type == "step_update":
        if (
            not state.initialized
            or state.result is not None
            or not isinstance(event.get("step_update"), dict)
        ):
            _record_protocol(state, "Antigravity step_update event is malformed.")
    elif event_type == "result":
        value = event.get("result")
        if state.result is not None or not isinstance(value, dict):
            _record_protocol(state, "Antigravity result event is malformed.")
        else:
            if not state.initialized:
                _record_protocol(state, "Antigravity result event is malformed.")
            state.result = value


def _record_protocol(state: _State, message: str) -> None:
    state.protocol_error = state.protocol_error or ResultError("protocol_error", message)


def _decode_result(value: dict[str, object]) -> DecodedOutput:
    status = value.get("status")
    output = value.get("response")
    if not isinstance(status, str) or not isinstance(output, str):
        return _protocol("Antigravity result envelope is malformed.")
    if status not in {
        "SUCCESS",
        "ERROR",
        "CANCELED",
        "INTERRUPTED",
        "INVALID",
        "WAITING",
        "RUNNING",
    }:
        return _protocol(f"Antigravity result has unknown status {status!r}.")
    decoded_usage = _usage(value.get("usage"))
    if isinstance(decoded_usage, ResultError):
        usage_error: ResultError | None = decoded_usage
        usage: Usage | None = None
    else:
        usage_error = None
        usage = decoded_usage
    if status != "SUCCESS":
        message = value.get("error")
        if message is not None and not isinstance(message, str):
            return DecodedOutput(output=output, error=_protocol_error("error field is malformed"))
        if status in {"WAITING", "RUNNING"}:
            return DecodedOutput(
                output=output,
                usage=usage,
                error=_protocol_error(f"result has nonterminal status {status!r}"),
            )
        return DecodedOutput(
            output=output,
            usage=usage,
            error=ResultError("provider_error", message or f"Antigravity reported {status}."),
        )
    if usage_error is not None:
        return DecodedOutput(output=output, error=usage_error)
    return DecodedOutput(output=output, usage=usage)


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict):
        return _protocol_error("usage is malformed")
    fields = {
        "input_tokens": "input_tokens",
        "cache_read_tokens": "cached_input_tokens",
        "output_tokens": "output_tokens",
        "thinking_tokens": "reasoning_output_tokens",
    }
    counts: dict[str, int] = {}
    for source, target in fields.items():
        count = value.get(source)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return _protocol_error(f"usage field {source!r} is malformed")
        counts[target] = count
    total = value.get("total_tokens")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return _protocol_error("usage field 'total_tokens' is malformed")
    return Usage(**counts)


def _protocol_error(detail: str) -> ResultError:
    return ResultError("protocol_error", f"Antigravity {detail}.")


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
