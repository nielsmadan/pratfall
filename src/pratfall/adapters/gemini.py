import json

from pratfall.adapters.accounting import model_map
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--debug",
        "-d",
        "--sandbox",
        "-s",
        "--screen-reader",
        "--skip-trust",
        "--yolo",
        "-y",
    )
} | {
    name: Flag(1)
    for name in (
        "--admin-policy",
        "--allowed-mcp-server-names",
        "--allowed-tools",
        "--approval-mode",
        "--extensions",
        "-e",
        "--include-directories",
        "--policy",
    )
}
_RESERVED = {
    name: Flag(0, joined=name == "-p")
    for name in ("--acp", "--experimental-acp", "-p", "--list-sessions")
} | {
    name: Flag(1, joined=name in {"-i", "-m", "-o", "-r", "-w"})
    for name in (
        "--delete-session",
        "--input-format",
        "-i",
        "-m",
        "--model",
        "-o",
        "--output-format",
        "--prompt",
        "--prompt-interactive",
        "-r",
        "--resume",
        "--worktree",
        "-w",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "--output-format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(arguments)
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Gemini", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as error:
        return _protocol(f"Invalid Gemini JSON: {error.msg}.")
    except ValueError:
        return _protocol("Invalid Gemini JSON: numeric value is too large.")
    if not isinstance(value, dict):
        return _protocol("Gemini result must be a JSON object.")
    stats = value.get("stats") if "stats" in value else None
    models_value = stats.get("models") if isinstance(stats, dict) else stats
    reported_models, model_error = model_map(models_value, "Gemini stats.models")
    output = value.get("response")
    if output is not None and not isinstance(output, str):
        return DecodedOutput(
            reported_models=reported_models,
            error=ResultError("protocol_error", "Gemini response must be a string when present."),
        )
    usage = _usage(stats) if "stats" in value else None
    if isinstance(usage, ResultError):
        usage_error: ResultError | None = usage
        usage = None
    else:
        usage_error = None
    native_error = value.get("error")
    provider_error = _provider_error(native_error) if "error" in value else None
    if provider_error is not None:
        timed_out = provider_error.code == "provider_error" and (
            isinstance(native_error, dict)
            and isinstance(native_error.get("type"), str)
            and "timeout" in native_error["type"].lower()
        )
        result_error = (
            ResultError("timeout", provider_error.message) if timed_out else provider_error
        )
        return DecodedOutput(
            output=output or "",
            usage=usage,
            reported_models=reported_models,
            error=result_error,
            timed_out=timed_out,
        )
    protocol_error = usage_error or model_error
    if protocol_error is not None:
        return DecodedOutput(
            output=output or "",
            usage=usage,
            reported_models=reported_models,
            error=protocol_error,
        )
    if not isinstance(output, str):
        return DecodedOutput(
            usage=usage,
            reported_models=reported_models,
            error=ResultError("protocol_error", "Gemini result is missing a response or error."),
        )
    return DecodedOutput(output=output, usage=usage, reported_models=reported_models)


def _provider_error(value: object) -> ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Gemini error field is malformed.")
    error_type = value.get("type")
    message = value.get("message")
    code = value.get("code")
    if (
        not isinstance(error_type, str)
        or not isinstance(message, str)
        or (code is not None and (isinstance(code, bool) or not isinstance(code, str | int)))
    ):
        return ResultError("protocol_error", "Gemini error field is malformed.")
    return ResultError("provider_error", message or f"Gemini reported {error_type}.")


def _usage(value: object) -> Usage | ResultError | None:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Gemini stats must be a JSON object.")
    models = value.get("models")
    if not isinstance(models, dict):
        return ResultError("protocol_error", "Gemini stats.models must be a JSON object.")
    if not models:
        return None
    totals = dict.fromkeys(("input", "cached", "candidates", "thoughts"), 0)
    for model in models.values():
        if not isinstance(model, dict) or not isinstance(model.get("tokens"), dict):
            return ResultError("protocol_error", "Gemini model token statistics are malformed.")
        tokens = model["tokens"]
        for name in ("input", "prompt", "candidates", "cached", "thoughts", "tool", "total"):
            count = tokens.get(name)
            if not _token_count(count):
                return ResultError("protocol_error", f"Gemini token field {name!r} is malformed.")
            if name in totals:
                totals[name] += count
    return Usage(
        input_tokens=totals["input"],
        cached_input_tokens=totals["cached"],
        output_tokens=totals["candidates"],
        reasoning_output_tokens=totals["thoughts"],
    )


def _token_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
