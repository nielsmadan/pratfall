from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
)
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile

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

    @property
    def type_field(self) -> str:
        return "role"

    def apply(self, event: dict[str, object]) -> Activity | None:
        content = event.get("content")
        if event["role"] != "assistant" or not isinstance(content, str):
            self.malformed("Malformed Kimi final assistant message.")
            return None
        self._text = self.retain.text("answer", content)
        return "answering"

    def result(self) -> DecodedOutput:
        return DecodedOutput(output=self._text.decode("utf-8"), error=self.protocol_error)
