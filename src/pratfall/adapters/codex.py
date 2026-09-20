import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from pratfall.adapters.native_args import Flag, fail_native_argument, validate_flags
from pratfall.adapters.whole_json import session_text
from pratfall.consumer import (
    DEFAULT_CONSUMER_LIMITS,
    ConsumerFailure,
    ConsumerLimits,
    JsonlConsumer,
    decode_with,
)
from pratfall.errors import PratError
from pratfall.limits import JSON_DEPTH
from pratfall.models import Activity, DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage
from pratfall.schema import StructuredAnswer, parse_json, retain_answer

_TURN_SLOTS = ("answer", "join", "answer_id", "turn")

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--approve-for-me",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "--oss",
        "--skip-git-repo-check",
        "--strict-config",
    )
} | {
    name: Flag(1, joined=name in {"-c", "-i", "-p", "-s"})
    for name in (
        "--add-dir",
        "--config",
        "-c",
        "--disable",
        "--enable",
        "--image",
        "-i",
        "--local-provider",
        "--output-schema",
        "--profile",
        "-p",
        "--sandbox",
        "-s",
        "--thread-source",
    )
}
_RESERVED = {name: Flag(0) for name in ("--json",)} | {
    name: Flag(1, joined=name in {"-C", "-m", "-o"})
    for name in (
        "--cd",
        "-C",
        "--color",
        "--model",
        "-m",
        "--output-last-message",
        "-o",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "exec", "--json"]
    options = resolved.options
    if options.instructions is not None:
        value = json.dumps(options.instructions, ensure_ascii=False).replace("\x7f", "\\u007f")
        argv.extend(("-c", f"developer_instructions={value}"))
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("-c", f"model_reasoning_effort={json.dumps(options.effort)}"))
    if options.fast is not None:
        service_tier = "priority" if options.fast else "default"
        argv.extend(("-c", f"service_tier={json.dumps(service_tier)}"))
    for directory in resolved.options.add_dirs or ():
        argv.extend(("--add-dir", directory))
    for attachment in resolved.options.attachments or ():
        argv.append(f"--image={attachment}")
    if resolved.prepared_schema is not None:
        if resolved.prepared_schema.path is None:
            raise ValueError("Prepared schema requires a file snapshot.")
        argv.extend(("--output-schema", resolved.prepared_schema.path))
    argv.extend(arguments)
    if options.attachments:
        argv.append("--")
    argv.append("-")
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED | (
        {"--add-dir": _ALLOWED["--add-dir"]} if resolved.options.add_dirs else {}
    )
    if resolved.options.schema is not None:
        reserved |= {"--output-schema": _ALLOWED["--output-schema"]}
    if resolved.options.attachments:
        reserved |= {name: _ALLOWED[name] for name in ("--image", "-i")}
        for path in resolved.options.attachments:
            if "," in path:
                raise PratError(
                    f"Codex attachment path {path!r} cannot contain commas.",
                    code="invalid_arguments",
                    option="attachments",
                )
    validate_flags("Codex", resolved.options.native_args or (), _ALLOWED, reserved)
    _reject_owned_config_keys(resolved)


def _reject_owned_config_keys(resolved: ResolvedProfile) -> None:
    options = resolved.options
    owned = set()
    if options.instructions is not None or options.instructions_file is not None:
        owned.add("developer_instructions")
    if options.model is not None:
        owned.add("model")
    if options.effort is not None:
        owned.add("model_reasoning_effort")
    if options.fast is not None:
        owned.add("service_tier")
    if not owned:
        return
    for argument, value in _config_overrides(options.native_args or ()):
        key = value.split("=", 1)[0].strip()
        if key in owned or any(key.startswith(f"{name}.") for name in owned):
            fail_native_argument("Codex", argument, "this configuration key is controlled by prat")


def _config_overrides(arguments: tuple[str, ...]) -> Iterator[tuple[str, str]]:
    # Runs after validate_flags, which guarantees every separated -c is followed by a value.
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-c", "--config"}:
            yield f"{argument} {arguments[index + 1]}", arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--config="):
            yield argument, argument.removeprefix("--config=")
        elif argument.startswith("-c") and argument != "-c":
            # Clap drops one separator after a short flag, so -c=KEY=VALUE sets KEY.
            yield argument, argument.removeprefix("-c").removeprefix("=")
        index += 1


@dataclass
class _State:
    answers: list[bytes] = field(default_factory=list)
    current_answer: bytes | None = None
    usage: Usage | None = None
    completed: bool = False
    provider_error: ResultError | None = None
    current_answer_id: str | None = None
    session_id: str | None = None


def decode(stdout: str) -> DecodedOutput:
    return decode_with(consumer, stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _Consumer(limits)


class _Consumer(JsonlConsumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__("Codex", limits)
        self.state = _State()

    def apply(self, event: dict[str, object]) -> Activity | None:
        return _apply_event(self, event)

    def result(self) -> DecodedOutput:
        state = self.state
        output_parts = state.answers.copy()
        if state.current_answer is not None:
            output_parts.append(state.current_answer)
        output = b"\n".join(output_parts).decode("utf-8")
        failure = state.provider_error or self.protocol_error
        if failure is not None:
            return DecodedOutput(
                output=output, session_id=state.session_id, usage=state.usage, error=failure
            )
        if not state.completed:
            if self.failed:
                return DecodedOutput(output=output, session_id=state.session_id, usage=state.usage)
            message = "Codex stream ended without turn.completed."
            try:
                self.malformed(message)
            except ConsumerFailure as budget_failure:
                decoded = DecodedOutput(
                    output=output,
                    session_id=state.session_id,
                    usage=state.usage,
                    error=budget_failure.error,
                )
                raise ConsumerFailure(budget_failure.error, decoded) from budget_failure
            return DecodedOutput(
                output=output,
                session_id=state.session_id,
                usage=state.usage,
                error=self.protocol_error,
            )
        return DecodedOutput(output=output, session_id=state.session_id, usage=state.usage)


def _apply_event(consumer: _Consumer, event: dict[str, object]) -> Activity | None:
    state = consumer.state
    event_type = event["type"]
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        if not isinstance(thread_id, str):
            consumer.malformed("Codex thread.started event is malformed.")
        else:
            session_id = session_text(thread_id)
            if session_id is not None:
                consumer.retain.text("session", session_id)
                state.session_id = session_id
        return "starting"
    elif event_type == "turn.started":
        consumer.retain.release(*_TURN_SLOTS)
        state.current_answer = None
        state.current_answer_id = None
        state.completed = False
        return "working"
    elif event_type in {"item.started", "item.updated", "item.completed"}:
        return _apply_item(consumer, event, event_type)
    elif event_type == "turn.completed":
        decoded_usage = _usage(event.get("usage"))
        if isinstance(decoded_usage, ResultError):
            consumer.malformed(decoded_usage.message)
        else:
            consumer.retain.usage("usage", decoded_usage)
            state.usage = decoded_usage
            consumer.retain.record("turn")
            state.completed = True
            if state.current_answer is not None:
                state.answers.append(state.current_answer)
            state.current_answer = None
            state.current_answer_id = None
            consumer.retain.release("answer_id")
            consumer.retain.commit(*_TURN_SLOTS)
        return "finishing"
    elif event_type == "turn.failed":
        error = _event_error(event.get("error"), "Codex turn failed.")
        _set_failure(consumer, error)
        return "finishing"
    elif event_type == "error":
        error = _event_error(event, "Codex reported an error.")
        _set_failure(consumer, error)
        return "finishing"
    return None


def _apply_item(
    consumer: _Consumer, event: dict[str, object], event_type: object
) -> Activity | None:
    state = consumer.state
    item = event.get("item")
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not isinstance(item.get("type"), str)
    ):
        consumer.malformed(f"Codex {event_type} event is malformed.")
        return "working"
    item_id = item["id"]
    if event_type == "item.completed" and item["type"] == "agent_message":
        if state.current_answer_id != item_id:
            consumer.retain.text("answer_id", item_id)
            consumer.retain.record("turn")
            state.current_answer_id = item_id
        text = item.get("text")
        if not isinstance(text, str):
            consumer.malformed("Codex agent_message item is malformed.")
        else:
            # the join byte outlives same-identifier answer replacement
            if state.current_answer is None and state.answers:
                consumer.retain.payload("join", 1)
            state.current_answer = consumer.retain.text("answer", text)
            state.completed = False
        return "answering"
    item_type = item["type"]
    if item_type in {"reasoning"}:
        return "reasoning"
    if item_type in {"command_execution", "mcp_tool_call", "web_search"}:
        return "tool"
    return "working"


def _set_failure(consumer: _Consumer, error: ResultError) -> None:
    state = consumer.state
    if error.code == "provider_error":
        if state.provider_error is None:
            consumer.retain.text("provider", error.message)
            state.provider_error = error
    else:
        consumer.malformed(error.message)


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Codex turn.completed usage is malformed.")
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    optional = ("cache_write_input_tokens", "reasoning_output_tokens")
    values: dict[str, int | None] = {}
    for name in (*required, *optional):
        item = value.get(name)
        if name in optional and item is None:
            values[name] = None
            continue
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            return ResultError("protocol_error", f"Codex usage field {name!r} is malformed.")
        values[name] = item
    return Usage(**values)


def _event_error(value: object, fallback: str) -> ResultError:
    if not isinstance(value, Mapping) or not isinstance(value.get("message"), str):
        return ResultError("protocol_error", "Codex failure event is malformed.")
    return ResultError("provider_error", value["message"] or fallback)


def schema_consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return _SchemaConsumer(limits)


def decode_schema(stdout: str) -> DecodedOutput:
    return decode_with(schema_consumer, stdout)


class _SchemaConsumer(_Consumer):
    def __init__(self, limits: ConsumerLimits) -> None:
        super().__init__(limits)
        self.answer: StructuredAnswer | None = None
        self.numeric_bytes = limits.numeric_bytes
        self.current_valid = False

    def parse_record(self, line: str) -> object:
        return parse_json(line, max_depth=JSON_DEPTH + 2, numeric_bytes=self.numeric_bytes)

    def apply(self, event: dict[str, object]) -> Activity | None:
        event_type = event["type"]
        if event_type == "turn.started":
            self.current_valid = False
            self.state.completed = False
            return "working"
        if event_type == "turn.completed":
            usage = _usage(event.get("usage"))
            if isinstance(usage, ResultError):
                self.malformed(usage.message)
            else:
                self.retain.usage("usage", usage)
                self.state.usage = usage
                self.state.completed = True
            return "finishing"
        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                self.state.completed = False
                self.current_valid = False
                if not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str):
                    self.malformed("Codex agent_message item is malformed.")
                    return "answering"
                try:
                    value = parse_json(item["text"], numeric_bytes=self.numeric_bytes)
                except UnicodeEncodeError as error:
                    raise ConsumerFailure(
                        ResultError("output_encoding", "Codex answer contains invalid Unicode.")
                    ) from error
                except ValueError:
                    return "answering"
                self.answer = retain_answer(value, self.retain)
                self.current_valid = True
                return "answering"
        return super().apply(event)

    def result(self) -> DecodedOutput:
        error = self.state.provider_error or self.protocol_error
        if error is None and not self.failed:
            if not self.state.completed:
                error = ResultError("protocol_error", "Codex stream ended without turn.completed.")
            elif not self.current_valid:
                error = ResultError(
                    "protocol_error", "Codex final agent_message is missing or is not strict JSON."
                )
        return DecodedOutput(
            output=self.answer.output if self.answer is not None else "",
            session_id=self.state.session_id,
            structured_output=self.answer.value if self.answer is not None else None,
            structured_output_present=self.answer is not None,
            usage=self.state.usage,
            error=error,
        )
