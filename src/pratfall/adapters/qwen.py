from pratfall.adapters.accounting import model
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {name: Flag(0) for name in ("--debug", "-d")} | {
    name: Flag(1) for name in ("--approval-mode", "--system-prompt", "--append-system-prompt")
}
_RESERVED = {
    name: Flag(0, joined=name in {"-c"})
    for name in ("--continue", "-c", "--acp", "--experimental-acp", "--fork-session")
} | {
    name: Flag(1, joined=name in {"-p", "-i", "-m", "-o", "-r", "-w"})
    for name in (
        "--prompt",
        "-p",
        "--prompt-interactive",
        "-i",
        "--input-format",
        "--output-format",
        "-o",
        "--model",
        "-m",
        "--max-session-turns",
        "--max-wall-time",
        "--max-tool-calls",
        "--resume",
        "-r",
        "--session-id",
        "--worktree",
        "-w",
        "--cwd",
        "--config",
        "--fallback-model",
        "--effort",
        "--json-schema",
    )
}
_QWEN_RESULT_ERRORS = {"error_during_execution", "error_max_turns"}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "--output-format", "stream-json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.max_turns is not None:
        argv.extend(("--max-session-turns", str(resolved.options.max_turns)))
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Qwen", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Qwen", limits)
        self.output = b""
        self.usage: Usage | None = None
        self.models: list[str] = []
        self.provider_error: ResultError | None = None
        self.protocol_error: ResultError | None = None
        self.completed = False
        self.result_last = False
        self.has_output = False
        self.has_result = False

    def malformed(self, message: str) -> None:
        if self.protocol_error is None:
            self.budget.add_string(message)
            self.protocol_error = ResultError("protocol_error", message)

    def apply(self, event: dict[str, object]) -> str | None:
        event_type = event["type"]
        self.result_last = event_type == "result"
        if event_type == "assistant":
            self._assistant(event)
            return "answering"
        if event_type == "result":
            self._result(event)
            return "finishing"
        if event_type == "system":
            if not isinstance(event.get("subtype"), str):
                self.malformed("Qwen system event is malformed.")
        elif event_type == "stream_event":
            partial = event.get("event")
            if not isinstance(partial, dict) or not isinstance(partial.get("type"), str):
                self.malformed("Qwen stream event is malformed.")
        elif event_type != "user":
            self.malformed("Unknown Qwen event type.")
        return None

    def _assistant(self, event: dict[str, object]) -> None:
        parent = event.get("parent_tool_use_id")
        if "parent_tool_use_id" not in event or not (parent is None or isinstance(parent, str)):
            self.malformed("Qwen assistant parent is malformed.")
            return
        if parent is not None:
            return
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            self.malformed("Qwen assistant message is malformed.")
            return
        reported, error = model(message.get("model"), "Qwen assistant model")
        if error is not None:
            self.malformed(error.message)
        elif reported is not None and reported not in self.models:
            self.budget.replace_state(0, len(retained_utf8(reported)), 1)
            self.models.append(reported)
        texts: list[str] = []
        for block in message["content"]:
            if (
                not isinstance(block, dict)
                or not isinstance(block.get("type"), str)
                or block["type"]
                not in {
                    "text",
                    "thinking",
                    "tool_use",
                    "tool_result",
                }
            ):
                self.malformed("Qwen assistant content is malformed.")
                return
            if block["type"] == "text":
                if not isinstance(block.get("text"), str):
                    self.malformed("Qwen assistant text is malformed.")
                    return
                texts.append(block["text"])
        if texts:
            self._text("".join(texts))

    def _text(self, text: str) -> None:
        encoded = retained_utf8(text)
        self.budget.replace_state(len(self.output), len(encoded), int(not self.has_output))
        self.output = encoded
        self.has_output = True

    def _result(self, event: dict[str, object]) -> None:
        subtype = event.get("subtype")
        error_flag = event.get("is_error")
        error_value = event.get("error")
        if subtype == "success" and error_flag is False and "error" not in event:
            failure = None
        elif isinstance(subtype, str) and subtype in _QWEN_RESULT_ERRORS and error_flag is True:
            if not isinstance(error_value, dict) or not isinstance(error_value.get("message"), str):
                self.malformed("Qwen result error must contain a message string.")
                return
            failure = ResultError("provider_error", error_value["message"] or "Qwen failed.")
        else:
            self.malformed("Qwen result subtype or error flag is malformed.")
            return
        valid_result = False
        try:
            if failure is None:
                text = event.get("result")
                if not isinstance(text, str):
                    self.malformed("Qwen result text is malformed.")
                    return
                self._text(text)
            valid_result = True
        finally:
            self._retain_result(
                event.get("usage"), failure if valid_result else self.provider_error
            )
        self.completed = failure is None

    def _retain_result(self, value: object, failure: ResultError | None) -> None:
        usage = _usage(value)
        if isinstance(usage, ResultError):
            self.malformed(usage.message)
            usage = None
        old_size = self._result_size(self.usage, self.provider_error)
        new_size = self._result_size(usage, failure)
        self.budget.replace_state(old_size, new_size, int(not self.has_result))
        self.usage = usage
        self.provider_error = failure
        self.has_result = True

    def _result_size(self, usage: Usage | None, error: ResultError | None) -> int:
        size = len(retained_utf8(error.message)) if error is not None else 0
        if usage is not None:
            size += sum(self.budget.numeric_size(value) for value in usage.__dict__.values())
        return size

    def result(self) -> DecodedOutput:
        error = self.provider_error or self.protocol_error
        if error is None and not (self.completed and self.result_last) and not self.failed:
            error = ResultError(
                "protocol_error", "Qwen stream ended without a final success result."
            )
        return DecodedOutput(
            output=self.output.decode("utf-8"),
            usage=self.usage,
            reported_models=tuple(self.models) or None,
            error=error,
        )


def _usage(value: object) -> Usage | ResultError | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Qwen result usage is malformed.")
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens")
    for field in fields:
        count = value.get(field)
        if field == "cache_read_input_tokens" and field not in value:
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return ResultError("protocol_error", f"Qwen usage {field} is malformed.")
    return Usage(
        input_tokens=value["input_tokens"],
        output_tokens=value["output_tokens"],
        cached_input_tokens=value.get("cache_read_input_tokens"),
    )
