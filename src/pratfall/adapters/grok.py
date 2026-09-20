import math

from pratfall.adapters.accounting import cost, model_map
from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.whole_json import document_error, encoding_error, parse, session_text
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--always-approve",
        "--dangerously-skip-permissions",
        "--disable-web-search",
        "--experimental-memory",
        "--no-memory",
        "--no-plan",
        "--no-subagents",
        "--no-wait-for-background",
        "--verbatim",
        "--yolo",
    )
} | {
    name: Flag(1)
    for name in (
        "--agent",
        "--agents",
        "--allow",
        "--allowedTools",
        "--append-system-prompt",
        "--background-wait-timeout",
        "--deny",
        "--disallowed-tools",
        "--disallowedTools",
        "--json-schema",
        "--permission-mode",
        "--rules",
        "--sandbox",
        "--system-prompt",
        "--system-prompt-override",
        "--tools",
    )
}
_RESERVED = {
    name: Flag(0, joined=name in {"-c", "-p", "-v", "-V"})
    for name in (
        "-c",
        "--continue",
        "--fork-session",
        "--include-partial-messages",
        "--no-auto-update",
        "-p",
        "--print",
        "--single",
        "-v",
        "-V",
        "--version",
    )
} | {
    name: Flag(1, joined=name in {"-m", "-r", "-s", "-w"})
    for name in (
        "--cwd",
        "--effort",
        "--load",
        "-m",
        "--max-turns",
        "--model",
        "--output-format",
        "--prompt-file",
        "--prompt-json",
        "--reasoning-effort",
        "-r",
        "--ref",
        "--resume",
        "-s",
        "--session-id",
        "-w",
        "--worktree",
        "--worktree-ref",
    )
}
_STOP_REASONS = {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}
_USAGE_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    options = resolved.options
    argv = [*resolved.command, "--no-auto-update", "--output-format", "json"]
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("--reasoning-effort", options.effort))
    if options.max_turns is not None:
        argv.extend(("--max-turns", str(options.max_turns)))
    if options.session_id is not None:
        argv.extend(("--session-id", options.session_id))
    argv.extend(options.native_args or ())
    argv.append(f"--single={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Grok Build", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Grok")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return DecodedOutput(error=_protocol("Grok result must be a JSON object."))
    if value.get("type") == "error":
        return _decode_error(value)

    usage, reported_models, cost_usd, accounting_error = _accounting(value)
    output = value.get("text")
    if not isinstance(output, str):
        return DecodedOutput(
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=_protocol("Grok result envelope is malformed."),
        )
    try:
        output.encode("utf-8")
    except UnicodeEncodeError:
        output = ""
        output_error: ResultError | None = encoding_error()
    else:
        output_error = None
    stop_reason = value.get("stopReason")
    session = value.get("sessionId")
    if (
        "type" in value
        or not isinstance(stop_reason, str)
        or stop_reason not in _STOP_REASONS
        or not isinstance(session, str)
        or not isinstance(value.get("requestId"), str)
    ):
        return DecodedOutput(
            output=output,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=output_error or _protocol("Grok result envelope is malformed."),
        )

    session_id = session_text(session)
    if stop_reason != "end_turn":
        return DecodedOutput(
            output=output,
            session_id=session_id,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=ResultError("provider_error", f"Grok run ended with {stop_reason}."),
        )
    return DecodedOutput(
        output=output,
        session_id=session_id,
        usage=usage,
        reported_models=reported_models,
        cost_usd=cost_usd,
        error=output_error or accounting_error or document_error(value, "Grok"),
    )


def _decode_error(value: dict[str, object]) -> DecodedOutput:
    message = value.get("message")
    if not isinstance(message, str) or not message:
        return DecodedOutput(error=_protocol("Grok error result is missing a string message."))
    try:
        message.encode("utf-8")
    except UnicodeEncodeError:
        return DecodedOutput(error=encoding_error())
    usage, reported_models, cost_usd, _ = _accounting(value)
    return DecodedOutput(
        usage=usage,
        reported_models=reported_models,
        cost_usd=cost_usd,
        error=ResultError("provider_error", message),
    )


def _accounting(
    value: dict[str, object],
) -> tuple[Usage | None, tuple[str, ...] | None, int | float | None, ResultError | None]:
    usage, usage_error = _usage(value.get("usage"))
    reported_models, model_error = model_map(value.get("modelUsage"), "Grok modelUsage")
    cost_usd, cost_error = cost(value.get("total_cost_usd"), "Grok total_cost_usd")
    metadata_error = _accounting_metadata(value, cost_usd)
    incomplete = value.get("usage_is_incomplete")
    if incomplete is True:
        usage = None
        cost_usd = None
        usage_error = _protocol("Grok usage is incomplete.")
    return (
        usage,
        reported_models,
        cost_usd,
        (usage_error or model_error or cost_error or metadata_error),
    )


def _usage(value: object) -> tuple[Usage | None, ResultError | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, _protocol("Grok usage must be a JSON object.")
    counts: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            return None, _protocol(f"Grok usage field {field!r} is malformed.")
        counts[field] = item
    expected_total = sum(counts[field] for field in _USAGE_FIELDS[:4])
    if counts["total_tokens"] != expected_total:
        return None, _protocol("Grok usage total_tokens is inconsistent.")
    return (
        Usage(
            input_tokens=counts["input_tokens"],
            cached_input_tokens=counts["cache_read_input_tokens"],
            cache_write_input_tokens=counts["cache_creation_input_tokens"],
            output_tokens=counts["output_tokens"],
            reasoning_output_tokens=counts["reasoning_tokens"],
        ),
        None,
    )


def _accounting_metadata(
    value: dict[str, object], cost_usd: int | float | None
) -> ResultError | None:
    for field in ("usage_is_incomplete", "cost_is_partial"):
        item = value.get(field)
        if item is not None and not isinstance(item, bool):
            return _protocol(f"Grok {field} is malformed.")
    turns = value.get("num_turns")
    if turns is not None and (not isinstance(turns, int) or isinstance(turns, bool) or turns < 0):
        return _protocol("Grok num_turns is malformed.")
    ticks = value.get("total_cost_usd_ticks")
    if ticks is not None and (not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0):
        return _protocol("Grok total_cost_usd_ticks is malformed.")
    if value.get("cost_is_partial") is True and (cost_usd is not None or ticks is not None):
        return _protocol("Grok partial cost includes a total.")
    if (cost_usd is None) != (ticks is None):
        return _protocol("Grok cost fields are incomplete.")
    if (
        cost_usd is not None
        and ticks is not None
        and not math.isclose(float(cost_usd), ticks / 10_000_000_000, rel_tol=0, abs_tol=5e-11)
    ):
        return _protocol("Grok cost fields are inconsistent.")
    return None


def _protocol(message: str) -> ResultError:
    return ResultError("protocol_error", message)
