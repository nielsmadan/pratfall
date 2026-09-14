import json
import math

from pratfall.adapters.accounting import cost, model
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.errors import PratError
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--auth-env-only",
        "--isolated",
        "--local-model-lean",
        "--no-auth-env-only",
    )
} | {name: Flag(1) for name in ("--code-mode", "--config", "--fallback")}
_RESERVED = {name: Flag(0) for name in ("--json",)} | {
    name: Flag(1)
    for name in (
        "--cwd",
        "--message",
        "--message-file",
        "--model",
        "--state-dir",
        "--thinking",
        "--timeout",
        "-m",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "agent", "exec", "--json", "--message-file", "-"]
    options = resolved.options
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("--thinking", options.effort))
    if options.timeout is not None:
        argv.extend(("--timeout", str(math.ceil(options.timeout))))
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    arguments = resolved.options.native_args or ()
    validate_flags("OpenClaw", arguments, _ALLOWED, _RESERVED)
    if _has_fallback(arguments) and resolved.options.model is None:
        raise PratError(
            "OpenClaw native --fallback requires an explicit model override.",
            code="invalid_arguments",
        )


def decode(stdout: str) -> DecodedOutput:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as error:
        return _protocol(f"Invalid OpenClaw JSON: {error.msg}.")
    except ValueError:
        return _protocol("Invalid OpenClaw JSON: numeric value is too large.")
    except RecursionError:
        return _protocol("Invalid OpenClaw JSON: document nesting is too deep.")
    if not isinstance(value, dict):
        return _protocol("OpenClaw result must be a JSON object.")
    return _decode_envelope(value)


def _decode_envelope(value: dict[str, object]) -> DecodedOutput:
    reported_models, cost_usd, accounting_error = _accounting(value)
    status = value.get("status")
    ok = value.get("ok")
    final = value.get("final")
    payloads = _payloads(value.get("payloads"))
    if (
        not isinstance(ok, bool)
        or not isinstance(status, str)
        or status not in {"ok", "error", "timeout"}
        or not isinstance(final, str)
    ):
        return DecodedOutput(
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=ResultError("protocol_error", "OpenClaw result envelope is malformed."),
        )
    if isinstance(payloads, ResultError):
        return DecodedOutput(
            output=final,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=payloads,
        )
    usage = _usage(value.get("usage")) if "usage" in value else None
    if isinstance(usage, ResultError):
        usage_error: ResultError | None = usage
        usage = None
    else:
        usage_error = None
    native_error = _error(value["error"]) if "error" in value else None
    payload_error = _payload_error(payloads)
    provider_error = _provider_error(native_error, payload_error)
    if status == "timeout":
        failure = provider_error or native_error or usage_error or accounting_error
        if failure is None:
            decoded = DecodedOutput(
                output=final,
                usage=usage,
                reported_models=reported_models,
                cost_usd=cost_usd,
                error=_missing_error(),
            )
        elif failure.code != "provider_error":
            decoded = DecodedOutput(
                output=final,
                usage=usage,
                reported_models=reported_models,
                cost_usd=cost_usd,
                error=failure,
            )
        else:
            decoded = DecodedOutput(
                output=final,
                usage=usage,
                reported_models=reported_models,
                cost_usd=cost_usd,
                error=ResultError("timeout", failure.message),
                timed_out=True,
            )
    elif provider_error is not None:
        decoded = DecodedOutput(
            output=final,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=provider_error,
        )
    elif native_error is not None or usage_error is not None or accounting_error is not None:
        decoded = DecodedOutput(
            output=final,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=native_error or usage_error or accounting_error,
        )
    elif status == "ok" and ok:
        decoded = DecodedOutput(
            output=final,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
        )
    elif status != "ok" and not ok:
        decoded = DecodedOutput(
            output=final,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=_missing_error(),
        )
    else:
        decoded = DecodedOutput(
            output=final,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=_envelope_mismatch(),
        )
    return decoded


def _accounting(
    value: dict[str, object],
) -> tuple[tuple[str, ...] | None, int | float | None, ResultError | None]:
    reported_model, model_error = model(value.get("model"), "OpenClaw model")
    reported_provider, provider_error = model(value.get("provider"), "OpenClaw provider")
    if reported_model is None or provider_error is not None:
        reported_models = None
    else:
        identifier = (
            f"{reported_provider}/{reported_model}"
            if reported_provider is not None
            else reported_model
        )
        reported_models = (identifier,)
    cost_usd, cost_error = cost(value.get("costUsd"), "OpenClaw costUsd")
    return reported_models, cost_usd, model_error or provider_error or cost_error


def _has_fallback(arguments: tuple[str, ...]) -> bool:
    return any(
        argument == "--fallback" or argument.startswith("--fallback=") for argument in arguments
    )


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "OpenClaw usage is malformed.")
    counts = {name: value.get(name) for name in ("input", "output", "total")}
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in counts.values()
    ):
        return ResultError("protocol_error", "OpenClaw usage is malformed.")
    return Usage(input_tokens=counts["input"], output_tokens=counts["output"])


def _payloads(value: object) -> tuple[dict[str, object], ...] | ResultError:
    if not isinstance(value, list):
        return ResultError("protocol_error", "OpenClaw payloads are malformed.")
    payloads: list[dict[str, object]] = []
    for payload in value:
        if not isinstance(payload, dict) or not _valid_payload(payload):
            return ResultError("protocol_error", "OpenClaw payloads are malformed.")
        payloads.append(payload)
    return tuple(payloads)


def _valid_payload(payload: dict[object, object]) -> bool:
    if "text" in payload and not isinstance(payload["text"], str):
        return False
    if "mediaUrl" in payload and not (
        isinstance(payload["mediaUrl"], str) or payload["mediaUrl"] is None
    ):
        return False
    media_urls = payload.get("mediaUrls")
    if "mediaUrls" in payload and (
        not isinstance(media_urls, list) or not all(isinstance(url, str) for url in media_urls)
    ):
        return False
    return all(
        name not in payload or isinstance(payload[name], bool)
        for name in ("isError", "isReasoning", "isCommentary")
    )


def _payload_error(payloads: tuple[dict[str, object], ...]) -> ResultError | None:
    for payload in payloads:
        if payload.get("isError") is True:
            message = payload.get("text")
            if isinstance(message, str) and message.strip():
                return ResultError("provider_error", message)
            return ResultError("provider_error", "OpenClaw reported an error payload.")
    return None


def _error(value: object) -> ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "OpenClaw error is malformed.")
    message = value.get("message")
    kind = value.get("kind")
    if not isinstance(message, str) or not isinstance(kind, str):
        return ResultError("protocol_error", "OpenClaw error is malformed.")
    return ResultError("provider_error", message or f"OpenClaw reported {kind}.")


def _provider_error(*errors: ResultError | None) -> ResultError | None:
    return next(
        (error for error in errors if error is not None and error.code == "provider_error"), None
    )


def _envelope_mismatch() -> ResultError:
    return ResultError("protocol_error", "OpenClaw ok and status fields disagree.")


def _missing_error() -> ResultError:
    return ResultError("protocol_error", "OpenClaw failure envelope is missing an error.")


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
