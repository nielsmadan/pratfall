from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {name: Flag(0) for name in ("--thinking", "--no-thinking", "--plan", "--debug")}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--print",
        "--quiet",
        "--final-message-only",
        "--continue",
        "-C",
        "--acp",
        "--wire",
    )
} | {
    name: Flag(1, joined=name in {"-p", "-c", "-m", "-w", "-S", "-r"})
    for name in (
        "--prompt",
        "-p",
        "--command",
        "-c",
        "--input-format",
        "--output-format",
        "--model",
        "-m",
        "--config",
        "--config-file",
        "--work-dir",
        "-w",
        "--session",
        "--resume",
        "-S",
        "-r",
        "--agent",
        "--agent-file",
        "--remote",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [
        *resolved.command,
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--final-message-only",
    ]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Kimi", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Kimi", limits)
        self._text = b""
        self._error: ResultError | None = None

    @property
    def type_field(self) -> str:
        return "role"

    def apply(self, event: dict[str, object]) -> str | None:
        content = event.get("content")
        if event["role"] != "assistant" or not isinstance(content, str):
            self.malformed("Malformed Kimi final assistant message.")
            return None
        encoded = retained_utf8(content)
        self.budget.replace_bytes(len(self._text), len(encoded))
        self._text = encoded
        return "answering"

    def malformed(self, message: str) -> None:
        if self._error is None:
            self.budget.add_string(message)
            self._error = ResultError("protocol_error", message)

    def result(self) -> DecodedOutput:
        return DecodedOutput(output=self._text.decode("utf-8"), error=self._error)
