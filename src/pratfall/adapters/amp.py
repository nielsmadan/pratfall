from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {"--stream-json-thinking": Flag(0)}
_RESERVED = {
    name: Flag(0, joined=name in {"-x", "-o"})
    for name in ("--execute", "-x", "--stream-json", "--stream-json-input", "--no-tui", "-o")
} | {
    name: Flag(1)
    for name in (
        "--model",
        "--effort",
        "--mode",
        "--executor",
        "--runner-id",
        "--cwd",
        "--config",
        "--settings-file",
        "--thread",
        "--resume",
        "--output-format",
        "--prompt",
        "--max-turns",
    )
}
_ERRORS = {"error_during_execution", "error_max_turns"}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    return Invocation((*resolved.command, "--execute", "--stream-json", *arguments), prompt)


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Amp", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Amp", limits)
        self.output = b""
        self.usage: Usage | None = None
        self.provider_error: ResultError | None = None
        self.protocol_error: ResultError | None = None
        self.completed = False
        self.has_output = False
        self.has_result = False

    def malformed(self, message: str) -> None:
        if self.protocol_error is None:
            self.budget.add_string(message)
            self.protocol_error = ResultError("protocol_error", message)

    def apply(self, event: dict[str, object]) -> str | None:
        if self.completed:
            self.malformed("Amp emitted a record after its terminal result.")
            return None
        event_type = event["type"]
        if event_type == "assistant":
            self._assistant(event)
            return "answering"
        if event_type == "result":
            self._result(event)
            return "finishing"
        if event_type == "system":
            subtype = event.get("subtype")
            if isinstance(subtype, str) and subtype in _ERRORS:
                self._error(event.get("error"))
            elif subtype != "init":
                self.malformed("Amp system subtype is malformed.")
        elif event_type != "user":
            self.malformed("Unknown Amp event type.")
        return None

    def _assistant(self, event: dict[str, object]) -> None:
        parent = event.get("parent_tool_use_id")
        if "parent_tool_use_id" not in event or not (parent is None or isinstance(parent, str)):
            self.malformed("Amp assistant parent is malformed.")
            return
        if parent is not None:
            return
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            self.malformed("Amp assistant message is malformed.")
            return
        texts: list[str] = []
        for block in message["content"]:
            if (
                not isinstance(block, dict)
                or not isinstance(block.get("type"), str)
                or block["type"]
                not in {
                    "text",
                    "thinking",
                    "redacted_thinking",
                    "tool_use",
                }
            ):
                self.malformed("Amp assistant content is malformed.")
                return
            if block["type"] == "text":
                if not isinstance(block.get("text"), str):
                    self.malformed("Amp assistant text is malformed.")
                    return
                texts.append(block["text"])
        if texts:
            self._text("".join(texts))

    def _text(self, text: str) -> None:
        encoded = retained_utf8(text)
        self.budget.replace_state(len(self.output), len(encoded), int(not self.has_output))
        self.output = encoded
        self.has_output = True

    def _error(self, value: object) -> None:
        if not isinstance(value, str):
            self.malformed("Amp error must be a string.")
        elif self.provider_error is None:
            message = value or "Amp failed."
            self.budget.add_string(message)
            self.provider_error = ResultError("provider_error", message)

    def _result(self, event: dict[str, object]) -> None:
        subtype = event.get("subtype")
        if subtype == "success" and event.get("is_error") is False and "error" not in event:
            try:
                text = event.get("result")
                if not isinstance(text, str):
                    self.malformed("Amp result text is malformed.")
                    return
                self._text(text)
            finally:
                self._retain_usage(event.get("usage"))
        elif isinstance(subtype, str) and subtype in _ERRORS and event.get("is_error") is True:
            self._error(event.get("error"))
            self._retain_usage(event.get("usage"))
        else:
            self.malformed("Amp result subtype or error flag is malformed.")
            return
        self.completed = True

    def _retain_usage(self, value: object) -> None:
        usage = _usage(value)
        if isinstance(usage, ResultError):
            self.malformed(usage.message)
        else:
            old_size = (
                sum(self.budget.numeric_size(value) for value in self.usage.__dict__.values())
                if self.usage is not None
                else 0
            )
            size = (
                sum(self.budget.numeric_size(value) for value in usage.__dict__.values())
                if usage is not None
                else 0
            )
            self.budget.replace_state(old_size, size, int(not self.has_result))
            self.usage = usage
            self.has_result = True

    def result(self) -> DecodedOutput:
        error = self.provider_error or self.protocol_error
        if error is None and not self.completed and not self.failed:
            error = ResultError("protocol_error", "Amp stream ended without a terminal result.")
        return DecodedOutput(output=self.output.decode("utf-8"), usage=self.usage, error=error)


def _usage(value: object) -> Usage | ResultError | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Amp result usage is malformed.")
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    for field in fields:
        count = value.get(field)
        if field.startswith("cache_") and field not in value:
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return ResultError("protocol_error", f"Amp usage {field} is malformed.")
    return Usage(
        input_tokens=value["input_tokens"],
        output_tokens=value["output_tokens"],
        cached_input_tokens=value.get("cache_read_input_tokens"),
        cache_write_input_tokens=value.get("cache_creation_input_tokens"),
    )
