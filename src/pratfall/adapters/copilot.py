import math
from dataclasses import dataclass, field

from pratfall.adapters.accounting import model
from pratfall.adapters.native_args import Flag, validate_flags, validate_tool_values
from pratfall.adapters.whole_json import session_text
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerFailure,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile, ResultError

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
    argv = [*resolved.command, "--output-format=json"]
    options = resolved.options
    if options.model is not None:
        argv.append(f"--model={options.model}")
    if options.effort is not None:
        argv.append(f"--effort={options.effort}")
    if options.max_ai_credits is not None:
        argv.append(f"--max-ai-credits={options.max_ai_credits}")
    if options.session_id is not None:
        argv.append(f"--session-id={options.session_id}")
    for directory in options.add_dirs or ():
        argv.append(f"--add-dir={directory}")
    if options.tools is not None:
        if options.tools:
            argv.append("--available-tools=" + ",".join(options.tools))
        else:
            argv.append("--available-tools")
    if options.disabled_tools:
        argv.append("--excluded-tools=" + ",".join(options.disabled_tools))
    if options.native_agent is not None:
        argv.append(f"--agent={options.native_agent}")
    for attachment in resolved.options.attachments or ():
        argv.append(f"--attachment={attachment}")
    argv.extend(arguments)
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED | (
        {"--add-dir": _ALLOWED["--add-dir"]} if resolved.options.add_dirs else {}
    )
    if resolved.options.attachments:
        reserved |= {"--attachment": _ALLOWED["--attachment"]}
    if resolved.options.native_agent is not None:
        reserved |= {"--agent": _ALLOWED["--agent"]}
    validate_tool_values(resolved.options.tools, "tools", comma=True, trim=True)
    validate_tool_values(resolved.options.disabled_tools, "disabled_tools", comma=True, trim=True)
    if resolved.options.tools is not None:
        reserved |= {
            name: _ALLOWED[name]
            for name in (
                "--available-tools",
                "--enable-mcp-server",
                "--enable-all-github-mcp-tools",
                "--add-github-mcp-tool",
                "--add-github-mcp-toolset",
            )
        }
    if resolved.options.disabled_tools:
        reserved |= {"--excluded-tools": _ALLOWED["--excluded-tools"]}
    validate_flags("Copilot", resolved.options.native_args or (), _ALLOWED, reserved)


@dataclass
class _State:
    order: list[str] = field(default_factory=list)
    messages: dict[str, bytes | bytearray] = field(default_factory=dict)
    complete_messages: set[str] = field(default_factory=set)
    completed: bool = False
    terminal_seen: bool = False
    provider_error: ResultError | None = None
    recognized: bool = False
    reported_models: list[str] = field(default_factory=list)
    reported_model_set: set[str] = field(default_factory=set)
    session_id: str | None = None


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Copilot", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> Activity | None:
        return _apply_event(self, event)

    def result(self) -> DecodedOutput:
        state = self.state
        output = b"\n".join(state.messages[item] for item in state.order).decode("utf-8")
        models = tuple(state.reported_models) or None
        failure = state.provider_error or self.protocol_error
        if failure is not None:
            return DecodedOutput(
                output=output, session_id=state.session_id, reported_models=models, error=failure
            )
        if not state.completed:
            if self.failed:
                return DecodedOutput(
                    output=output, session_id=state.session_id, reported_models=models
                )
            suffix = "" if state.recognized else " (only unknown events were received)"
            message = f"Copilot stream ended without a successful result event{suffix}."
            try:
                self.malformed(message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output,
                    session_id=state.session_id,
                    reported_models=models,
                    error=budget_failure.error,
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
            return DecodedOutput(
                output=output,
                session_id=state.session_id,
                reported_models=models,
                error=self.protocol_error,
            )
        return DecodedOutput(output=output, session_id=state.session_id, reported_models=models)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> Activity | None:
    state = consumer.state
    event_type = event["type"]
    if event_type in {"assistant.message", "assistant.message_delta"}:
        state.recognized = True
        _message(consumer, event, event_type)
        return "answering"
    elif event_type == "session.error":
        state.recognized = True
        _session_error(consumer, event)
        return "finishing"
    elif event_type == "session.warning":
        state.recognized = True
        _session_warning(consumer, event)
        return "working"
    elif event_type == "result":
        state.recognized = True
        _result(consumer, event)
        return "finishing"
    if event_type in {"assistant.reasoning", "assistant.reasoning_delta"}:
        return "reasoning"
    if event_type in {"tool.execution_start", "tool.execution_complete"}:
        return "tool"
    return None


def _message(consumer: _Consumer, event: dict[str, object], event_type: object) -> None:
    state = consumer.state
    data = event.get("data")
    if not isinstance(data, dict):
        consumer.malformed(f"Copilot {event_type} event is malformed.")
        return
    agent_id = event.get("agentId")
    parent_tool_call = data.get("parentToolCallId")
    if (agent_id is not None and not isinstance(agent_id, str)) or (
        parent_tool_call is not None and not isinstance(parent_tool_call, str)
    ):
        consumer.malformed(f"Copilot {event_type} event is malformed.")
        return
    if agent_id is not None or parent_tool_call is not None:
        return
    message_id = data.get("messageId")
    field = "content" if event_type == "assistant.message" else "deltaContent"
    content = data.get(field)
    if not isinstance(message_id, str) or not isinstance(content, str):
        consumer.malformed(f"Copilot {event_type} event is malformed.")
        return
    encoded = retained_utf8(content)
    new_message = message_id not in state.messages
    if new_message:
        separator = 1 if state.order else 0
        consumer.retain.text(f"id:{message_id}", message_id, extra=separator, record=True)
        consumer.retain.payload(f"text:{message_id}", len(encoded))
        state.order.append(message_id)
        state.messages[message_id] = (
            encoded if event_type == "assistant.message" else bytearray(encoded)
        )
    if event_type == "assistant.message":
        if not new_message:
            consumer.retain.payload(f"text:{message_id}", len(encoded))
            state.messages[message_id] = encoded
        state.complete_messages.add(message_id)
        reported_model, model_error = model(data.get("model"), "Copilot assistant model")
        if model_error is not None:
            consumer.malformed(model_error.message)
        elif reported_model is not None and reported_model not in state.reported_model_set:
            consumer.retain.text(f"model:{reported_model}", reported_model, record=True)
            state.reported_models.append(reported_model)
            state.reported_model_set.add(reported_model)
    elif message_id in state.complete_messages:
        return
    elif encoded and not new_message:
        message = state.messages[message_id]
        if isinstance(message, bytearray):
            consumer.retain.payload(f"text:{message_id}", len(message) + len(encoded))
            message.extend(encoded)


def _session_error(consumer: _Consumer, event: dict[str, object]) -> None:
    data = event.get("data")
    if not isinstance(data, dict):
        consumer.malformed("Copilot session.error event is malformed.")
        return
    error_type = data.get("errorType")
    message = data.get("message")
    if not isinstance(error_type, str) or not isinstance(message, str):
        consumer.malformed("Copilot session.error event is malformed.")
    elif error_type != "model_call":
        _provider(consumer, message or f"Copilot reported {error_type}.")


def _session_warning(consumer: _Consumer, event: dict[str, object]) -> None:
    data = event.get("data")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("warningType"), str)
        or not isinstance(data.get("message"), str)
    ):
        consumer.malformed("Copilot session.warning event is malformed.")
        return
    warning_type = data["warningType"]
    if warning_type in _TERMINAL_WARNINGS:
        message = data.get("message")
        _provider(consumer, message or f"Copilot reported {warning_type}.")


def _result(consumer: _Consumer, event: dict[str, object]) -> None:
    state = consumer.state
    session = event.get("sessionId")
    exit_code = event.get("exitCode")
    usage = event.get("usage")
    if (
        not isinstance(event.get("timestamp"), str)
        or not isinstance(session, str)
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code not in {0, 1}
        or not isinstance(usage, dict)
        or not _summary_usage(usage)
    ):
        consumer.malformed("Copilot result event is malformed.")
        return
    if state.terminal_seen:
        consumer.malformed("Copilot result event is malformed.")
    else:
        consumer.retain.record("result")
        state.terminal_seen = True
        session_id = session_text(session)
        if session_id is not None:
            consumer.retain.text("session", session_id)
            state.session_id = session_id
    if exit_code == 0:
        state.completed = True
    else:
        _provider(consumer, "Copilot reported an unsuccessful result.")


def _provider(consumer: _Consumer, message: str) -> None:
    if consumer.state.provider_error is None:
        consumer.retain.text("provider", message)
        consumer.state.provider_error = ResultError("provider_error", message)


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
