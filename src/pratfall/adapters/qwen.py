from dataclasses import replace

from pratfall.adapters.accounting import model
from pratfall.adapters.native_args import (
    Flag,
    validate_directory_values,
    validate_flags,
    validate_tool_values,
)
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
    retained_utf8,
)
from pratfall.errors import PratError
from pratfall.limits import JSON_DEPTH
from pratfall.models import (
    Activity,
    DecodedOutput,
    Invocation,
    PreparedSchema,
    ResolvedProfile,
    ResultError,
    Usage,
)
from pratfall.schema import StructuredAnswer, parse_json, retain_answer

_ALLOWED = {name: Flag(0) for name in ("--debug", "-d")} | {
    name: Flag(1)
    for name in (
        "--approval-mode",
        "--system-prompt",
        "--append-system-prompt",
        "--include-directories",
        "--add-dir",
    )
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
    if resolved.options.instructions is not None:
        argv.append(f"--append-system-prompt={resolved.options.instructions}")
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.max_turns is not None:
        argv.extend(("--max-session-turns", str(resolved.options.max_turns)))
    for directory in resolved.options.add_dirs or ():
        argv.extend(("--include-directories", directory))
    for tool in resolved.options.tools or ():
        argv.append(f"--core-tools={tool}")
    for tool in resolved.options.disabled_tools or ():
        argv.append(f"--exclude-tools={tool}")
    if resolved.prepared_schema is not None:
        if resolved.prepared_schema.path is None:
            raise ValueError("Prepared schema requires a file snapshot.")
        argv.extend(("--json-schema", "@" + resolved.prepared_schema.path))
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_directory_values(resolved.options.add_dirs or ())
    reserved = _RESERVED | (
        {
            "--include-directories": _ALLOWED["--include-directories"],
            "--add-dir": _ALLOWED["--add-dir"],
        }
        if resolved.options.add_dirs
        else {}
    )
    if resolved.options.instructions is not None or resolved.options.instructions_file is not None:
        reserved |= {"--append-system-prompt": _ALLOWED["--append-system-prompt"]}
    validate_tool_values(resolved.options.tools, "tools", comma=True, trim=True)
    validate_tool_values(resolved.options.disabled_tools, "disabled_tools", comma=True, trim=True)
    if resolved.options.tools is not None:
        reserved |= {"--core-tools": Flag(1)}
    if resolved.options.disabled_tools:
        reserved |= {"--exclude-tools": Flag(1)}
    validate_flags("Qwen", resolved.options.native_args or (), _ALLOWED, reserved)


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
        self.completed = False
        self.result_last = False

    def apply(self, event: dict[str, object]) -> Activity | None:
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
            self.retain.text(f"model:{reported}", reported, record=True)
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
        self.output = self.retain.text("answer", text, record=True)

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
            if failure is None and not self._success(event):
                return
            valid_result = True
        finally:
            self._retain_result(
                event.get("usage"), failure if valid_result else self.provider_error
            )
        self.completed = failure is None

    def _success(self, event: dict[str, object]) -> bool:
        text = event.get("result")
        if not isinstance(text, str):
            self.malformed("Qwen result text is malformed.")
            return False
        self._text(text)
        return True

    def _retain_result(self, value: object, failure: ResultError | None) -> None:
        usage = _usage(value)
        if isinstance(usage, ResultError):
            self.malformed(usage.message)
            usage = None
        message = "" if failure is None else failure.message
        self.retain.usage("result", usage, extra=len(retained_utf8(message)), record=True)
        self.usage = usage
        self.provider_error = failure

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


def validate_schema(prepared: PreparedSchema) -> None:
    value = prepared.value
    if isinstance(value, dict) and "$ref" in value:
        raise PratError("Qwen rejects root $ref; wrap it in allOf.", option="schema")
    if not _may_accept_object(value):
        raise PratError("Qwen schema root must accept objects.", option="schema")


def _may_accept_object(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        return True
    types = value.get("type")
    if isinstance(types, str) and types != "object":
        return False
    if isinstance(types, list) and "object" not in types:
        return False
    if "const" in value and not isinstance(value["const"], dict):
        return False
    enum = value.get("enum")
    if isinstance(enum, list) and not any(isinstance(item, dict) for item in enum):
        return False
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = value.get(keyword)
        if isinstance(branches, list):
            accepts = [_may_accept_object(branch) for branch in branches]
            if not (all(accepts) if keyword == "allOf" else any(accepts)):
                return False
    return True


def schema_consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _SchemaConsumer(limits)


def decode_schema(stdout: str) -> DecodedOutput:
    return decode_with(schema_consumer, stdout)


class _SchemaConsumer(_Consumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__(limits)
        self.answer: StructuredAnswer | None = None
        self.numeric_bytes = limits.numeric_bytes

    def parse_record(self, line: str) -> object:
        return parse_json(line, max_depth=JSON_DEPTH + 2, numeric_bytes=self.numeric_bytes)

    def apply(self, event: dict[str, object]) -> Activity | None:
        if event["type"] == "result" and event.get("parent_tool_use_id") is not None:
            self.result_last = False
            return None
        return super().apply(event)

    def _text(self, text: str) -> None:
        pass

    def _success(self, event: dict[str, object]) -> bool:
        if "structured_result" not in event:
            self.malformed("Qwen success is missing structured_result.")
            return False
        self.answer = retain_answer(event["structured_result"], self.retain)
        return True

    def result(self) -> DecodedOutput:
        decoded = super().result()
        if self.answer is None:
            return decoded
        return replace(
            decoded,
            output=self.answer.output,
            structured_output=self.answer.value,
            structured_output_present=True,
        )
