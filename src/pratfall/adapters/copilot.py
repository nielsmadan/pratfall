import json
import math
from dataclasses import dataclass, field

from pratfall.adapters.accounting import model
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = (
    {
        name: Flag(0)
        for name in (
            "--allow-all",
            "--allow-all-mcp-server-instructions",
            "--allow-all-paths",
            "--allow-all-tools",
            "--allow-all-urls",
            "--autopilot",
            "--disable-builtin-mcps",
            "--disallow-temp-dir",
            "--enable-all-github-mcp-tools",
            "--enable-memory",
            "--experimental",
            "--no-ask-user",
            "--no-auto-update",
            "--no-color",
            "--no-custom-instructions",
            "--no-experimental",
            "--no-remote",
            "--no-remote-export",
            "--no-sandbox",
            "--plain-diff",
            "--sandbox",
            "--yolo",
        )
    }
    | {
        name: Flag(1)
        for name in (
            "--add-dir",
            "--add-github-mcp-tool",
            "--add-github-mcp-toolset",
            "--agent",
            "--attachment",
            "--context",
            "--disable-mcp-server",
            "--enable-mcp-server",
            "--log-level",
            "--max-autopilot-continues",
            "--mode",
            "--plugin-dir",
        )
    }
    | {
        name: Flag(0, variadic=True)
        for name in (
            "--allow-tool",
            "--allow-url",
            "--available-tools",
            "--deny-tool",
            "--deny-url",
            "--excluded-tools",
            "--secret-env-vars",
        )
    }
)
_RESERVED = {
    name: Flag(0, joined=name in {"-p", "-s"})
    for name in (
        "--continue",
        "--no-banner",
        "-p",
        "--silent",
        "-s",
    )
} | {
    name: Flag(1, joined=name in {"-C", "-i", "-n", "-r", "-w"})
    for name in (
        "-C",
        "--connect",
        "--effort",
        "-i",
        "--interactive",
        "--max-ai-credits",
        "--model",
        "-n",
        "--name",
        "--output-format",
        "--prompt",
        "--reasoning-effort",
        "-r",
        "--resume",
        "--session-id",
        "--share",
        "--share-gist",
        "--stream",
        "-w",
        "--worktree",
    )
}
_TERMINAL_WARNINGS = frozenset({"compaction_static_context_blocked", "policy_blocked"})


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "--output-format=json"]
    options = resolved.options
    if options.model is not None:
        argv.append(f"--model={options.model}")
    if options.effort is not None:
        argv.append(f"--effort={options.effort}")
    if options.max_ai_credits is not None:
        argv.append(f"--max-ai-credits={options.max_ai_credits}")
    argv.extend(arguments)
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Copilot", arguments, _ALLOWED, _RESERVED)


@dataclass
class _State:
    order: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)
    complete_messages: set[str] = field(default_factory=set)
    completed: bool = False
    terminal_seen: bool = False
    provider_error: ResultError | None = None
    protocol_error: ResultError | None = None
    recognized: bool = False
    reported_models: list[str] = field(default_factory=list)


def decode(stdout: str) -> DecodedOutput:
    state = _State()
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            _protocol(state, f"Invalid Copilot JSONL on line {line_number}: {error.msg}.")
            continue
        except ValueError:
            _protocol(
                state, f"Invalid Copilot JSONL on line {line_number}: numeric value is too large."
            )
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            _protocol(state, f"Malformed Copilot event on line {line_number}.")
            continue
        _apply_event(state, event)
    output = "\n".join(state.messages[item] for item in state.order)
    failure = state.provider_error or state.protocol_error
    if failure is not None:
        return DecodedOutput(
            output=output,
            reported_models=tuple(state.reported_models) or None,
            error=failure,
        )
    if not state.completed:
        suffix = "" if state.recognized else " (only unknown events were received)"
        return DecodedOutput(
            output=output,
            reported_models=tuple(state.reported_models) or None,
            error=ResultError(
                "protocol_error", f"Copilot stream ended without a successful result event{suffix}."
            ),
        )
    return DecodedOutput(output=output, reported_models=tuple(state.reported_models) or None)


def _apply_event(state: _State, event: dict[str, object]) -> None:
    event_type = event["type"]
    if event_type in {"assistant.message", "assistant.message_delta"}:
        state.recognized = True
        _message(state, event, event_type)
    elif event_type == "session.error":
        state.recognized = True
        _session_error(state, event)
    elif event_type == "session.warning":
        state.recognized = True
        _session_warning(state, event)
    elif event_type == "result":
        state.recognized = True
        _result(state, event)


def _message(state: _State, event: dict[str, object], event_type: object) -> None:
    data = event.get("data")
    if not isinstance(data, dict):
        _protocol(state, f"Copilot {event_type} event is malformed.")
        return
    agent_id = event.get("agentId")
    parent_tool_call = data.get("parentToolCallId")
    if (agent_id is not None and not isinstance(agent_id, str)) or (
        parent_tool_call is not None and not isinstance(parent_tool_call, str)
    ):
        _protocol(state, f"Copilot {event_type} event is malformed.")
        return
    if agent_id is not None or parent_tool_call is not None:
        return
    message_id = data.get("messageId")
    field = "content" if event_type == "assistant.message" else "deltaContent"
    content = data.get(field)
    if not isinstance(message_id, str) or not isinstance(content, str):
        _protocol(state, f"Copilot {event_type} event is malformed.")
        return
    if message_id not in state.messages:
        state.order.append(message_id)
        state.messages[message_id] = ""
    if event_type == "assistant.message":
        state.messages[message_id] = content
        state.complete_messages.add(message_id)
        reported_model, model_error = model(data.get("model"), "Copilot assistant model")
        if model_error is not None:
            state.protocol_error = state.protocol_error or model_error
        elif reported_model is not None and reported_model not in state.reported_models:
            state.reported_models.append(reported_model)
    elif message_id in state.complete_messages:
        return
    else:
        state.messages[message_id] += content


def _session_error(state: _State, event: dict[str, object]) -> None:
    data = event.get("data")
    if not isinstance(data, dict):
        _protocol(state, "Copilot session.error event is malformed.")
        return
    error_type = data.get("errorType")
    message = data.get("message")
    if not isinstance(error_type, str) or not isinstance(message, str):
        _protocol(state, "Copilot session.error event is malformed.")
    elif error_type != "model_call":
        state.provider_error = state.provider_error or ResultError(
            "provider_error", message or f"Copilot reported {error_type}."
        )


def _session_warning(state: _State, event: dict[str, object]) -> None:
    data = event.get("data")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("warningType"), str)
        or not isinstance(data.get("message"), str)
    ):
        _protocol(state, "Copilot session.warning event is malformed.")
        return
    warning_type = data["warningType"]
    if warning_type in _TERMINAL_WARNINGS:
        message = data.get("message")
        state.provider_error = state.provider_error or ResultError(
            "provider_error", message or f"Copilot reported {warning_type}."
        )


def _result(state: _State, event: dict[str, object]) -> None:
    exit_code = event.get("exitCode")
    usage = event.get("usage")
    if (
        state.terminal_seen
        or not isinstance(event.get("timestamp"), str)
        or not isinstance(event.get("sessionId"), str)
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code not in {0, 1}
        or not isinstance(usage, dict)
        or not _summary_usage(usage)
    ):
        _protocol(state, "Copilot result event is malformed.")
        return
    state.terminal_seen = True
    if exit_code == 0:
        state.completed = True
    else:
        state.provider_error = state.provider_error or ResultError(
            "provider_error", "Copilot reported an unsuccessful result."
        )


def _protocol(state: _State, message: str) -> None:
    state.protocol_error = state.protocol_error or ResultError("protocol_error", message)


def _summary_usage(value: dict[str, object]) -> bool:
    code_changes = value.get("codeChanges")
    if not isinstance(code_changes, dict):
        return False
    numeric = (
        value.get("premiumRequests"),
        value.get("totalApiDurationMs"),
        value.get("sessionDurationMs"),
        code_changes.get("linesAdded"),
        code_changes.get("linesRemoved"),
        code_changes.get("filesModified"),
    )
    return all(
        isinstance(item, int | float)
        and not isinstance(item, bool)
        and math.isfinite(item)
        and item >= 0
        for item in numeric
    )
