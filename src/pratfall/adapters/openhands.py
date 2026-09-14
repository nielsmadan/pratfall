import json
import math
import re
from typing import NoReturn

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerFailure,
    ConsumerLimits,
    StateBudget,
    decode_with,
    retained_utf8,
)
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {"--override-with-envs": Flag(0)}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--headless",
        "--json",
        "--last",
        "--always-approve",
        "--yolo",
        "--llm-approve",
        "--exit-without-confirmation",
    )
} | {
    name: Flag(1, joined=name in {"-t", "-f"})
    for name in (
        "--task",
        "-t",
        "--file",
        "-f",
        "--resume",
        "--model",
        "--effort",
        "--config",
        "--cwd",
        "--session",
        "--output-format",
        "--executor",
    )
}
_IGNORED = frozenset(
    {
        "ACPToolCallEvent",
        "SystemPromptEvent",
        "TokenEvent",
        "ObservationEvent",
        "ObservationBaseEvent",
        "AgentErrorEvent",
        "UserRejectObservation",
        "PauseEvent",
        "StreamingDeltaEvent",
        "Condensation",
        "CondensationRequest",
        "CondensationSummaryEvent",
        "ConversationStateUpdateEvent",
        "HookExecutionEvent",
        "LLMCompletionLogEvent",
    }
)
_PROSE = frozenset(
    {
        "OpenHands CLI terminal UI may not work correctly in this environment: "
        "Rich detected a non-interactive or unsupported terminal; interactive UI may not render correctly",
        "To override Rich's detection, you can set TTY_INTERACTIVE=1 (and optionally TTY_COMPATIBLE=1).",
        "Initializing agent...",
        "✓ Hooks loaded",
    }
)
_SUMMARY = re.compile(r"[─━]+ CONVERSATION SUMMARY [─━]+")


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    return Invocation(
        (*resolved.command, "--headless", "--json", *arguments, f"--task={prompt.decode('utf-8')}"),
        b"",
    )


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("OpenHands", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> "_Consumer":
    return _Consumer(limits)


class _Consumer:
    def __init__(self, limits: ConsumerLimits) -> None:
        self._limits = limits
        self._budget = StateBudget(limits)
        self._buffer = bytearray()
        self._scan = 0
        self._finished = False
        self._failure: ConsumerFailure | None = None
        self._summary = False
        self._model_banner = False
        self._terminal = False
        self._text = b""
        self._error: ResultError | None = None

    def feed(self, data: bytes) -> str | None:
        if self._finished:
            raise RuntimeError("consumer already finished")
        if self._failure is not None:
            return None
        try:
            return self._feed(data)
        except ConsumerFailure as failure:
            self._failure = failure
            raise

    def _feed(self, data: bytes) -> str | None:
        self._buffer.extend(data)
        start = 0
        activity = None
        while (end := self._buffer.find(b"\n", self._scan)) >= 0:
            record = bytes(self._buffer[start:end])
            current = self._line(record.removesuffix(b"\r"))
            activity = current or activity
            start = end + 1
            self._scan = start
        self._scan = len(self._buffer) - start
        del self._buffer[:start]
        size = len(self._buffer)
        if size > self._limits.event_bytes + int(self._buffer.endswith(b"\r")):
            self._limit()
        return activity

    def finish(self) -> DecodedOutput:
        if self._finished:
            raise RuntimeError("consumer already finished")
        self._finished = True
        try:
            if self._failure is None and self._buffer:
                self._line(bytes(self._buffer))
            if self._failure is None and not self._terminal and self._error is None:
                self._protocol("OpenHands stream ended without a terminal assistant event.")
        except ConsumerFailure as failure:
            self._failure = failure
        finally:
            self._buffer.clear()
        decoded = DecodedOutput(output=self._text.decode("utf-8"), error=self._error)
        if self._failure is not None:
            raise ConsumerFailure(self._failure.error, decoded) from self._failure
        return decoded

    def _line(self, record: bytes) -> str | None:
        if len(record) > self._limits.event_bytes:
            self._limit()
        try:
            line = record.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ConsumerFailure(
                ResultError("output_encoding", "Agent stdout is not valid UTF-8.")
            ) from error
        handled, activity = self._framing(line)
        if handled:
            return activity
        try:
            event = json.loads(
                line,
                parse_int=self._integer,
                parse_float=self._float,
                parse_constant=self._constant,
            )
        except (ValueError, RecursionError):
            self._protocol("Invalid OpenHands event JSON or unexpected stdout text.")
            return None
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
            self._protocol("Malformed OpenHands event.")
            return None
        return self._event(event)

    def _framing(self, line: str) -> tuple[bool, str | None]:
        if self._summary or not line.strip():
            return True, None
        if _SUMMARY.fullmatch(line.strip()):
            self._summary = True
            return True, None
        if line in _PROSE:
            self._model_banner = False
            return True, None
        if line == "Agent is working":
            self._model_banner = False
            return True, "reasoning"
        if line == "Agent finished":
            self._model_banner = False
            return True, "finishing"
        if line.startswith("✓ Agent initialized with model:"):
            self._model_banner = True
            return True, None
        if self._model_banner and not line.lstrip().startswith(("{", "[")):
            return True, None
        self._model_banner = False
        return False, None

    def _event(self, event: dict[str, object]) -> str | None:
        kind = event["kind"]
        if kind == "ConversationErrorEvent":
            self._provider(event)
            return "finishing"
        if kind == "MessageEvent":
            self._message(event)
            return "answering" if event.get("source") == "agent" else None
        if kind == "ActionEvent":
            self._action(event)
            return "tool"
        if kind not in _IGNORED:
            self._protocol("Unknown OpenHands event kind.")
        return None

    def _message(self, event: dict[str, object]) -> None:
        source = event.get("source")
        message = event.get("llm_message")
        if not isinstance(source, str) or not isinstance(message, dict):
            self._protocol("Malformed OpenHands message event.")
            return
        if source != "agent":
            return
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            self._protocol("Malformed OpenHands assistant message.")
            return
        texts = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in ("text", "image"):
                self._protocol("Malformed OpenHands assistant content.")
                return
            if block["type"] == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    self._protocol("Malformed OpenHands assistant text.")
                    return
                texts.append(text)
        if not any(text.strip() for text in texts):
            return
        self._complete("".join(texts))

    def _action(self, event: dict[str, object]) -> None:
        action = event.get("action")
        if not isinstance(event.get("tool_name"), str) or (
            action is not None and not isinstance(action, dict)
        ):
            self._protocol("Malformed OpenHands action event.")
            return
        if event.get("tool_name") != "finish" and (
            not isinstance(action, dict) or action.get("kind") != "FinishAction"
        ):
            return
        if (
            event.get("source") != "agent"
            or event.get("tool_name") != "finish"
            or not isinstance(action, dict)
            or action.get("kind") != "FinishAction"
            or not isinstance(action.get("message"), str)
        ):
            self._protocol("Malformed OpenHands finish action.")
            return
        self._complete(action["message"])

    def _complete(self, text: str) -> None:
        encoded = retained_utf8(text)
        if self._terminal:
            self._protocol("OpenHands emitted more than one terminal assistant event.")
            return
        self._budget.replace_state(0, len(encoded), 1)
        self._text = encoded
        self._terminal = True

    def _provider(self, event: dict[str, object]) -> None:
        code, detail = event.get("code"), event.get("detail")
        if not isinstance(code, str) or not isinstance(detail, str):
            self._protocol("Malformed OpenHands conversation error.")
            return
        if self._error is not None and self._error.code == "provider_error":
            return
        message = detail or code or "OpenHands reported a conversation error."
        encoded = retained_utf8(message)
        previous = len(retained_utf8(self._error.message)) if self._error is not None else 0
        self._budget.replace_bytes(previous, len(encoded))
        self._error = ResultError("provider_error", message)

    def _protocol(self, message: str) -> None:
        if self._error is None:
            self._budget.add_string(message)
            self._error = ResultError("protocol_error", message)

    def _integer(self, value: str) -> int:
        self._numeric(value)
        return int(value)

    def _float(self, value: str) -> float:
        self._numeric(value)
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite number")
        return parsed

    def _numeric(self, value: str) -> None:
        if len(value) > self._limits.numeric_bytes:
            raise ValueError("numeric value is too large")

    @staticmethod
    def _constant(_value: str) -> NoReturn:
        raise ValueError("nonfinite number")

    def _limit(self) -> NoReturn:
        raise ConsumerFailure(
            ResultError(
                "stdout_limit_exceeded",
                f"Agent output event exceeded {self._limits.event_bytes} bytes.",
            )
        )
