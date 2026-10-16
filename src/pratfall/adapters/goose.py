from pratfall.adapters.accounting import cost
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.whole_json import document_error, encoding_error, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {"--provider": Flag(1)}
_RESERVED = {
    name: Flag(0)
    for name in ("--quiet", "-q", "--no-session", "--resume", "-r", "--interactive", "-s")
} | {
    name: Flag(1, joined=name in {"-i", "-t"})
    for name in (
        "--model",
        "--max-turns",
        "--output-format",
        "--instructions",
        "-i",
        "--text",
        "-t",
        "--recipe",
        "--system",
        "--params",
        "--sub-recipe",
        "--name",
        "--path",
        "--session-id",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    argv = [
        *resolved.command,
        "run",
        "--quiet",
        "--no-session",
        "--output-format",
        "json",
        "--instructions",
        "-",
    ]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.max_turns is not None:
        argv.extend(("--max-turns", str(resolved.options.max_turns)))
    argv.extend(resolved.options.native_args or ())
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Goose", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Goose")
    if isinstance(value, ResultError):
        if value.code == "protocol_error" and stdout.lstrip().startswith("error:"):
            return DecodedOutput(error=ResultError("provider_error", stdout.strip()))
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return DecodedOutput(error=_protocol("Goose result must be an object."))
    messages, metadata = value.get("messages"), value.get("metadata")
    if not isinstance(messages, list) or not isinstance(metadata, dict):
        return DecodedOutput(error=_protocol("Goose messages or metadata are malformed."))
    output = ""
    error = None
    for message in messages:
        text = _assistant_text(message)
        if isinstance(text, ResultError):
            error = error or text
        elif text:
            output = text
    status = metadata.get("status")
    if status == "error":
        error = ResultError("provider_error", "Goose reported a failed run.")
    elif status != "completed":
        error = error or _protocol("Goose result is missing completed status.")
    cost_usd, cost_error = cost(metadata.get("cost_usd"), "Goose cost_usd")
    return DecodedOutput(
        output=output,
        cost_usd=cost_usd,
        error=error or cost_error or document_error(value, "Goose"),
    )


def _assistant_text(message: object) -> str | ResultError | None:
    if not isinstance(message, dict) or not isinstance(message.get("role"), str):
        return _protocol("Goose message is malformed.")
    if message["role"] != "assistant":
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return _protocol("Goose assistant content is malformed.")
    texts = []
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            return _protocol("Goose content block is malformed.")
        if block["type"] == "text":
            text = block.get("text")
            if not isinstance(text, str):
                return _protocol("Goose text block is malformed.")
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                return encoding_error()
            texts.append(text)
    return "\n\n".join(texts)


def _protocol(message: str) -> ResultError:
    return ResultError("protocol_error", message)
