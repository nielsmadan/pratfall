from collections.abc import Mapping
from types import MappingProxyType

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import DEFAULT_CONSUMER_LIMITS, ConsumerLimits, JsonlConsumer, decode_with
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile

_ALLOWED: Mapping[str, Flag] = MappingProxyType(
    {name: Flag(1) for name in ("--skills-dir", "--add-dir")}
)
_RESERVED: Mapping[str, Flag] = MappingProxyType(
    {
        name: Flag(0)
        for name in (
            "--print",
            "--thinking",
            "--no-thinking",
            "--debug",
            "--final-message-only",
            "--plan",
            "--yolo",
            "--auto",
            "--continue",
            "--version",
            "-V",
            "--help",
        )
    }
    | {
        name: Flag(1, joined=name in {"-p", "-m", "-r"})
        for name in (
            "--prompt",
            "-p",
            "--model",
            "-m",
            "--output-format",
            "--input-format",
            "--agent",
            "--agent-file",
            "--resume",
            "-r",
            "--session",
            "--cwd",
            "--config",
        )
    }
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    argv = [*resolved.command, "--output-format", "stream-json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(resolved.options.native_args or ())
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Kimi Code", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Kimi Code", limits)
        self._text = b""
        self._session: str | None = None

    @property
    def type_field(self) -> str:
        return "role"

    def parse_record(self, line: str) -> object:
        event = super().parse_record(line)
        if isinstance(event, dict) and event.get("type") == "goal.summary" and "role" not in event:
            return {**event, "role": "meta"}
        return event

    def apply(self, event: dict[str, object]) -> Activity | None:
        role = event["role"]
        if role == "assistant":
            text = event.get("content", "")
            if not isinstance(text, str):
                self.malformed("Kimi Code assistant content is malformed.")
            elif text:
                self._text = self.retain.text("answer", text)
            return "answering"
        if role == "tool":
            return "tool"
        if role == "meta":
            return self._metadata(event)
        self.malformed("Unknown Kimi Code message role.")
        return None

    def _metadata(self, event: dict[str, object]) -> Activity | None:
        kind = event.get("type")
        if kind == "session.resume_hint":
            session = event.get("session_id")
            if not isinstance(session, str):
                self.malformed("Kimi Code session id is malformed.")
            else:
                self.retain.text("session", session)
                self._session = session
        elif kind == "turn.step.retrying":
            return "working"
        elif not isinstance(kind, str) or kind not in {"system.version", "goal.summary"}:
            self.malformed("Unknown Kimi Code metadata type.")
        return None

    def result(self) -> DecodedOutput:
        return DecodedOutput(
            output=self._text.decode("utf-8"), session_id=self._session, error=self.protocol_error
        )
