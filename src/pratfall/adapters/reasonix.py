from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.whole_json import document_error, encoding_error, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {"--show-thinking": Flag(0)} | {
    name: Flag(1) for name in ("--max-steps", "--permission-mode")
}
_RESERVED = {
    name: Flag(0, joined=name in {"-p", "-c"})
    for name in ("--print", "-p", "--continue", "-c", "--events-jsonl", "--copy", "--takeover")
} | {
    name: Flag(1)
    for name in (
        "--model",
        "--effort",
        "--output-format",
        "--dir",
        "--cwd",
        "--config",
        "--resume",
        "--prompt",
        "--profile",
        "--preset",
        "--metrics",
        "--trajectory",
    )
}
_COMPLETIONS = {
    "success": False,
    "incomplete_read": False,
    "recovery_paused": False,
    "completion_uncertain": False,
    "error_during_execution": True,
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "run", "--output-format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--effort", resolved.options.effort))
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Reasonix", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Reasonix")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return DecodedOutput(error=_protocol("Reasonix result must be a JSON object."))
    output = value.get("result")
    output_error = None
    if not isinstance(output, str):
        output = ""
        output_error = _protocol("Reasonix result text is malformed.")
    try:
        output.encode("utf-8")
    except UnicodeEncodeError:
        output = ""
        output_error = encoding_error()
    usage = _usage(value.get("usage"))
    usage_error = usage if isinstance(usage, ResultError) else None
    error = output_error or _completion(value) or document_error(value, "Reasonix") or usage_error
    return DecodedOutput(
        output=output,
        usage=usage if isinstance(usage, Usage) else None,
        error=error,
    )


def _completion(value: dict[str, object]) -> ResultError | None:
    subtype = value.get("subtype")
    if (
        value.get("type") != "result"
        or not isinstance(subtype, str)
        or subtype not in _COMPLETIONS
        or value.get("is_error") is not _COMPLETIONS[subtype]
    ):
        return _protocol("Reasonix result subtype or error flag is malformed.")
    if subtype != "success":
        return ResultError("provider_error", f"Reasonix run ended with {subtype}.")
    return None


def _usage(value: object) -> Usage | ResultError | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return _protocol("Reasonix result usage is malformed.")
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
        count = value.get(field)
        if field == "cache_read_input_tokens" and field not in value:
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return _protocol(f"Reasonix usage {field} is malformed.")
    return Usage(
        input_tokens=value["input_tokens"],
        output_tokens=value["output_tokens"],
        cached_input_tokens=value.get("cache_read_input_tokens"),
    )


def _protocol(message: str) -> ResultError:
    return ResultError("protocol_error", message)
