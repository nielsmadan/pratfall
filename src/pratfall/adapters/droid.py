from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.whole_json import document_error, encoding_error, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {"--auto": Flag(1)}
_RESERVED = {
    name: Flag(0)
    for name in ("--use-spec", "--mission", "--list-tools", "--skip-permissions-unsafe")
} | {
    name: Flag(1, joined=name in {"-o", "-f", "-s", "-m", "-r", "-w"})
    for name in (
        "--prompt",
        "--file",
        "-f",
        "--input-format",
        "--output-format",
        "-o",
        "--model",
        "-m",
        "--reasoning-effort",
        "-r",
        "--spec-model",
        "--spec-reasoning-effort",
        "--session-id",
        "-s",
        "--fork",
        "--cwd",
        "--config",
        "--worktree",
        "-w",
        "--worktree-dir",
        "--worker-model",
        "--worker-reasoning-effort",
        "--validator-model",
        "--validator-reasoning-effort",
        "--remote",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "exec", "--output-format", "json"]
    if resolved.options.instructions is not None:
        argv.append(f"--append-system-prompt={resolved.options.instructions}")
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--reasoning-effort", resolved.options.effort))
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED.copy()
    if resolved.options.instructions is not None or resolved.options.instructions_file is not None:
        reserved |= {"--append-system-prompt": Flag(1), "--append-system-prompt-file": Flag(1)}
    validate_flags("Droid", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Droid")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return DecodedOutput(error=_protocol("Droid result must be a JSON object."))
    output = value.get("result")
    output_error = None
    if not isinstance(output, str):
        output = ""
        output_error = _protocol("Droid result text is malformed.")
    try:
        output.encode("utf-8")
    except UnicodeEncodeError:
        output = ""
        output_error = encoding_error()
    return DecodedOutput(
        output=output,
        error=_completion(value) or output_error or document_error(value, "Droid"),
    )


def _completion(value: dict[str, object]) -> ResultError | None:
    if value.get("type") == "result" and value.get("is_error") is True:
        return ResultError("provider_error", "Droid reported a failed result.")
    if (
        value.get("type") != "result"
        or value.get("subtype") != "success"
        or value.get("is_error") is not False
    ):
        return _protocol("Droid result is missing a successful completion envelope.")
    return None


def _protocol(message: str) -> ResultError:
    return ResultError("protocol_error", message)
