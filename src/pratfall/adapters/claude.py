import json

from pratfall.adapters.accounting import cost, model_map
from pratfall.adapters.native_args import Flag, validate_flags, validate_tool_values
from pratfall.adapters.whole_json import document_error, encodable_text, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError, Usage

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--allow-dangerously-skip-permissions",
        "--bare",
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--no-chrome",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--verbose",
    )
} | {
    name: Flag(1)
    for name in (
        "--add-dir",
        "--agent",
        "--agents",
        "--allowedTools",
        "--allowed-tools",
        "--append-system-prompt",
        "--autocompact",
        "--betas",
        "--debug-file",
        "--disallowedTools",
        "--disallowed-tools",
        "--fallback-model",
        "--file",
        "--json-schema",
        "--mcp-config",
        "--permission-mode",
        "--permission-prompts",
        "--plugin-dir",
        "--plugin-url",
        "--setting-sources",
        "--system-prompt",
        "--tools",
    )
}
_RESERVED = {
    name: Flag(0, joined=name in {"-c", "-p"})
    for name in (
        "-c",
        "--continue",
        "--fork-session",
        "--forward-subagent-text",
        "--include-hook-events",
        "--include-partial-messages",
        "--no-session-persistence",
        "-p",
        "--print",
        "--replay-user-messages",
    )
} | {
    name: Flag(1, joined=name in {"-r", "-w"})
    for name in (
        "--effort",
        "--environment",
        "--input-format",
        "--max-budget-usd",
        "--max-turns",
        "--model",
        "--output-format",
        "--remote-control",
        "--resume",
        "--settings",
        "-r",
        "--session-id",
        "--worktree",
        "-w",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "-p", "--output-format", "json"]
    if resolved.options.instructions is not None:
        argv.append(f"--append-system-prompt={resolved.options.instructions}")
    options = resolved.options
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("--effort", options.effort))
    if options.max_budget_usd is not None:
        argv.extend(("--max-budget-usd", str(options.max_budget_usd)))
    if options.max_turns is not None:
        argv.extend(("--max-turns", str(options.max_turns)))
    if options.fast is not None:
        argv.extend(("--settings", json.dumps({"fastMode": options.fast})))
    for directory in resolved.options.add_dirs or ():
        argv.extend(("--add-dir", directory))
    if options.tools is not None:
        argv.append("--tools=" + ",".join(options.tools))
    if options.disabled_tools:
        argv.append("--disallowedTools=" + ",".join(options.disabled_tools))
    if options.native_agent is not None:
        argv.append(f"--agent={options.native_agent}")
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED | (
        {"--add-dir": _ALLOWED["--add-dir"]} if resolved.options.add_dirs else {}
    )
    if resolved.options.instructions is not None or resolved.options.instructions_file is not None:
        reserved |= {
            "--append-system-prompt": _ALLOWED["--append-system-prompt"],
            "--append-system-prompt-file": Flag(1),
        }
    if resolved.options.native_agent is not None:
        reserved |= {"--agent": _ALLOWED["--agent"]}
    validate_tool_values(resolved.options.tools, "tools", comma=True, whitespace=True, trim=True)
    validate_tool_values(resolved.options.disabled_tools, "disabled_tools", comma=True, trim=True)
    if resolved.options.tools is not None:
        reserved |= {"--tools": _ALLOWED["--tools"]}
    if resolved.options.disabled_tools:
        reserved |= {name: _ALLOWED[name] for name in ("--disallowedTools", "--disallowed-tools")}
    validate_flags("Claude Code", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Claude")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return _protocol("Claude result must be a JSON object.")
    reported_models, model_error = model_map(value.get("modelUsage"), "Claude modelUsage")
    cost_usd, cost_error = cost(value.get("total_cost_usd"), "Claude total_cost_usd")
    accounting_error = model_error or cost_error or document_error(value, "Claude")
    event_type = value.get("type")
    subtype = value.get("subtype")
    is_error = value.get("is_error")
    if event_type != "result" or not isinstance(subtype, str) or not isinstance(is_error, bool):
        return DecodedOutput(
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=ResultError("protocol_error", "Claude result envelope is malformed."),
        )
    decoded_usage = _usage(value.get("usage"))
    usage = None if isinstance(decoded_usage, ResultError) else decoded_usage
    usage_error = decoded_usage if isinstance(decoded_usage, ResultError) else None
    if subtype == "success":
        return _decode_success(
            value,
            is_error,
            usage,
            reported_models,
            cost_usd,
            usage_error or accounting_error,
        )
    if subtype in {
        "error_max_turns",
        "error_during_execution",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }:
        return _decode_failure(
            value,
            subtype,
            usage,
            reported_models,
            cost_usd,
            usage_error or accounting_error,
        )
    return DecodedOutput(
        usage=usage,
        reported_models=reported_models,
        cost_usd=cost_usd,
        error=ResultError("protocol_error", f"Claude result has unknown subtype {subtype!r}."),
    )


def _decode_success(
    value: dict[str, object],
    is_error: bool,
    usage: Usage | None,
    reported_models: tuple[str, ...] | None,
    cost_usd: int | float | None,
    protocol_error: ResultError | None,
) -> DecodedOutput:
    output = value.get("result")
    if not isinstance(output, str):
        return DecodedOutput(
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=protocol_error
            or ResultError(
                "protocol_error", "Claude success result is missing a string result field."
            ),
        )
    output = encodable_text(output)
    if is_error:
        message = output.strip() or "Claude reported an error."
        return DecodedOutput(
            output=output,
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=ResultError("provider_error", message),
        )
    return DecodedOutput(
        output=output,
        usage=usage,
        reported_models=reported_models,
        cost_usd=cost_usd,
        error=protocol_error,
    )


def _decode_failure(
    value: dict[str, object],
    subtype: str,
    usage: Usage | None,
    reported_models: tuple[str, ...] | None,
    cost_usd: int | float | None,
    protocol_error: ResultError | None,
) -> DecodedOutput:
    errors = value.get("errors")
    if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
        return DecodedOutput(
            usage=usage,
            reported_models=reported_models,
            cost_usd=cost_usd,
            error=protocol_error
            or ResultError(
                "protocol_error", "Claude error result is missing a string array errors field."
            ),
        )
    message = "\n".join(errors) or f"Claude reported {subtype}."
    return DecodedOutput(
        usage=usage,
        reported_models=reported_models,
        cost_usd=cost_usd,
        error=ResultError("provider_error", message),
    )


def _usage(value: object) -> Usage | ResultError:
    if not isinstance(value, dict):
        return ResultError("protocol_error", "Claude usage must be a JSON object.")
    fields = {
        "input_tokens": "input_tokens",
        "cache_read_input_tokens": "cached_input_tokens",
        "cache_creation_input_tokens": "cache_write_input_tokens",
        "output_tokens": "output_tokens",
    }
    numbers: dict[str, int | None] = {}
    for source, target in fields.items():
        item = value.get(source)
        if source in {"input_tokens", "output_tokens"} and item is None:
            return ResultError("protocol_error", f"Claude usage is missing {source!r}.")
        if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
            return ResultError("protocol_error", f"Claude usage field {source!r} is malformed.")
        numbers[target] = item
    return Usage(**numbers)


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
