from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {"--strict-mcp-startup": Flag(0)} | {
    name: Flag(1, joined=name == "-n") for name in ("--name", "-n", "--mcp-startup-timeout")
}
_RESERVED = {
    name: Flag(0) for name in ("--gui", "--share", "--cloud", "--sandboxed", "--skip-initial-turn")
} | {
    name: Flag(1, joined=name in {"-p", "-m", "-C", "-e"})
    for name in (
        "--prompt",
        "-p",
        "--saved-prompt",
        "--file",
        "--output-format",
        "--model",
        "-m",
        "--cwd",
        "-C",
        "--config",
        "--config-file",
        "--profile",
        "--conversation",
        "--environment",
        "-e",
        "--runner",
        "--executor",
        "--harness",
        "--task-id",
        "--idle-on-complete",
        "--idle-on-fail",
        "--session",
        "--resume",
    )
}
_IGNORED = frozenset(
    {
        "tool_result",
        "tool_canceled",
        "tool_error",
        "tool_call",
        "agent_reasoning",
        "update_todos",
        "complete_todos",
        "Subagent",
        "system",
        "num_comments_addressed",
        "artifact_created",
        "SkillInvoked",
    }
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "agent", "run", "--output-format", "ndjson"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(arguments)
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Warp", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Warp", limits)
        self._texts: list[bytes] = []
        self._error: ResultError | None = None

    def apply(self, event: dict[str, object]) -> str | None:
        event_type = event["type"]
        if event_type == "agent":
            text = event.get("text")
            if not isinstance(text, str):
                self.malformed("Malformed Warp agent text.")
                return None
            encoded = retained_utf8(text)
            self.budget.replace_state(0, len(encoded) + int(bool(self._texts)), 1)
            self._texts.append(encoded)
            return "answering"
        if event_type not in _IGNORED:
            self.malformed("Unknown Warp event type.")
        if event_type == "agent_reasoning":
            return "reasoning"
        if event_type in {"tool_result", "tool_canceled", "tool_error", "tool_call"}:
            return "tool"
        return None

    def malformed(self, message: str) -> None:
        if self._error is None:
            self.budget.add_string(message)
            self._error = ResultError("protocol_error", message)

    def result(self) -> DecodedOutput:
        return DecodedOutput(output=b"\n".join(self._texts).decode("utf-8"), error=self._error)
