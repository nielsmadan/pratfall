from pratfall.adapters.accounting import cost as native_cost
from pratfall.adapters.accounting import model as native_model
from pratfall.adapters.native_args import Flag, validate_flags, validate_tool_values
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
)
from pratfall.errors import PratError
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(1)
    for name in (
        "--provider",
        "--system-prompt",
        "--append-system-prompt",
        "--tools",
        "-t",
        "--exclude-tools",
        "-xt",
    )
} | {
    name: Flag(0)
    for name in (
        "--no-tools",
        "-nt",
        "--no-builtin-tools",
        "-nbt",
        "--no-extensions",
        "-ne",
        "--no-skills",
        "-ns",
        "--no-prompt-templates",
        "-np",
        "--no-context-files",
        "-nc",
        "--no-session",
        "--offline",
    )
}
_RESERVED = {name: Flag(0) for name in ("--print", "-p", "--continue", "-c", "--resume", "-r")} | {
    name: Flag(1)
    for name in (
        "--mode",
        "--model",
        "--thinking",
        "--session",
        "--session-id",
        "--session-dir",
        "--fork",
        "--export",
        "--models",
    )
}
_IGNORED = frozenset(
    {
        "session",
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "queue_update",
        "entry_appended",
        "session_info_changed",
        "thinking_level_changed",
        "compaction_start",
        "compaction_end",
        "auto_retry_start",
        "auto_retry_end",
        "summarization_retry_scheduled",
        "summarization_retry_attempt_start",
        "summarization_retry_finished",
        "bash_execution_update",
    }
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    options = resolved.options
    argv = [*resolved.command, "--print", "--mode", "json"]
    for flag, value in (("--model", options.model), ("--thinking", options.effort)):
        if value is not None:
            argv.extend((flag, value))
    if options.tools is not None:
        argv.extend(("--tools", ",".join(options.tools)))
    if options.disabled_tools:
        argv.extend(("--exclude-tools", ",".join(options.disabled_tools)))
    arguments = options.native_args or ()
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        name, equals, value = argument.partition("=")
        if equals and name in _ALLOWED and _ALLOWED[name].arity == 1:
            argv.extend((name, value))
        else:
            argv.append(argument)
            if name in _ALLOWED and _ALLOWED[name].arity == 1:
                index += 1
                argv.append(arguments[index])
        index += 1
    argv.extend(f"@{path}" for path in options.attachments or ())
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    options = resolved.options
    reserved = _RESERVED.copy()
    validate_tool_values(options.tools, "tools", comma=True, trim=True)
    validate_tool_values(options.disabled_tools, "disabled_tools", comma=True, trim=True)
    for path in options.attachments or ():
        if any(
            char
            in "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
            for char in path
        ):
            raise PratError(
                "Pi attachment paths cannot contain Unicode spaces that Pi normalizes.",
                code="invalid_arguments",
                option="attachments",
            )
    if options.tools is not None:
        reserved |= {
            name: _ALLOWED[name]
            for name in (
                "--tools",
                "-t",
                "--no-tools",
                "-nt",
                "--no-builtin-tools",
                "-nbt",
            )
        }
    if options.disabled_tools:
        reserved |= {name: _ALLOWED[name] for name in ("--exclude-tools", "-xt")}
    validate_flags("Pi", options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Pi", limits)
        self.output = b""
        self.usage: Usage | None = None
        self.models: tuple[str, ...] = ()
        self.cost: int | float | None = 0
        self.provider_error: ResultError | None = None
        self.completed = False
        self.assistant_seen = False
        self.usage_complete = True

    def apply(self, event: dict[str, object]) -> Activity | None:
        kind = event["type"]
        if self.completed:
            self.malformed("Pi emitted a record after agent_settled.")
            return None
        if kind == "agent_settled":
            self.completed = True
            return "finishing"
        if kind == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("role"), str):
                self.malformed("Pi message is malformed.")
            elif message["role"] == "assistant":
                self._assistant(message)
                return "answering"
        elif kind in {"message_start", "message_update"}:
            return "answering"
        elif kind in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
            return "tool"
        elif kind not in _IGNORED:
            self.malformed("Unknown Pi event type.")
        return None

    def _assistant(self, message: dict[str, object]) -> None:
        self.assistant_seen = True
        self._account(message)
        reason = message.get("stopReason")
        if reason not in ("stop", "length", "toolUse", "error", "aborted"):
            self.malformed("Pi assistant stop reason is malformed.")
            return
        if reason in ("error", "aborted"):
            error = message.get("errorMessage") or f"Pi request {reason}."
            if not isinstance(error, str):
                self.malformed("Pi assistant error is malformed.")
                return
            self.retain.text("provider", error)
            self.provider_error = ResultError("provider_error", error)
        else:
            self.retain.release("provider")
            self.provider_error = None
        content = message.get("content")
        if not isinstance(content, list):
            self.malformed("Pi assistant content is malformed.")
            return
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in (
                "text",
                "thinking",
                "toolCall",
            ):
                self.malformed("Pi assistant content block is malformed.")
                return
            if block["type"] == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    self.malformed("Pi assistant text is malformed.")
                    return
                texts.append(text)
        if texts or reason not in ("error", "aborted"):
            self.output = self.retain.text("answer", "".join(texts), record=True)

    def _account(self, message: dict[str, object]) -> None:
        model, error = native_model(message.get("model"), "Pi model")
        if error is not None:
            self.malformed(error.message)
        elif model is not None and model not in self.models:
            self.retain.text(f"model:{len(self.models)}", model, record=True)
            self.models += (model,)
        value = message.get("usage")
        usage = _usage(value)
        if isinstance(usage, ResultError):
            self.malformed(usage.message)
            self.usage_complete = False
        elif usage is None:
            self.usage_complete = False
        elif self.usage_complete:
            old = self.usage or Usage(0, 0, 0, 0)
            total_usage = Usage(
                (old.input_tokens or 0) + (usage.input_tokens or 0),
                (old.cached_input_tokens or 0) + (usage.cached_input_tokens or 0),
                (old.cache_write_input_tokens or 0) + (usage.cache_write_input_tokens or 0),
                (old.output_tokens or 0) + (usage.output_tokens or 0),
            )
            self.retain.usage("usage", total_usage)
            self.usage = total_usage
        raw_cost = value.get("cost") if isinstance(value, dict) else None
        total_cost = raw_cost.get("total") if isinstance(raw_cost, dict) else None
        cost, error = native_cost(total_cost, "Pi USD cost")
        if error is not None:
            self.malformed(error.message)
        total = self.cost + cost if self.cost is not None and cost is not None else None
        total, error = native_cost(total, "Pi total USD cost")
        if error is not None:
            self.malformed(error.message)
        self.retain.numbers("cost", (total,))
        self.cost = total

    def result(self) -> DecodedOutput:
        error = self.provider_error or self.protocol_error
        if error is None and not self.failed and not (self.completed and self.assistant_seen):
            error = ResultError("protocol_error", "Pi stream ended without a settled answer.")
        return DecodedOutput(
            output=self.output.decode("utf-8"),
            usage=self.usage if self.usage_complete else None,
            reported_models=self.models or None,
            cost_usd=self.cost if self.assistant_seen else None,
            error=error,
        )


def _usage(value: object) -> Usage | ResultError | None:
    if value is None:
        return None
    if isinstance(value, dict):
        counts: list[int] = []
        fields = ("input", "cacheRead", "cacheWrite", "output")
        for key in fields:
            count = value.get(key)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                break
            counts.append(count)
        if len(counts) == len(fields):
            return Usage(*counts)
    return ResultError("protocol_error", "Pi usage is malformed.")
