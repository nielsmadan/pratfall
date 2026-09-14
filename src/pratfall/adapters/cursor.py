from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.whole_json import document_error, encodable_text, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--approve-mcps",
        "--force",
        "-f",
        "--plan",
        "--skip-worktree-setup",
        "--trust",
        "--yolo",
    )
} | {
    name: Flag(1, joined=name == "-H")
    for name in (
        "--header",
        "-H",
        "--mode",
        "--plugin-dir",
        "--sandbox",
        "--worktree-base",
    )
}
_RESERVED = {
    name: Flag(0, joined=name == "-p")
    for name in ("--continue", "-p", "--print", "--stream-partial-output")
} | {
    name: Flag(1, joined=name == "-w")
    for name in (
        "--model",
        "--output-format",
        "--resume",
        "--workspace",
        "--worktree",
        "-w",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "--print", "--output-format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(arguments)
    argv.extend(("agent", "--", prompt.decode("utf-8")))
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Cursor", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Cursor")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, dict):
        return _protocol("Cursor result must be a JSON object.")
    output = value.get("result")
    if (
        value.get("type") != "result"
        or value.get("subtype") != "success"
        or value.get("is_error") is not False
        or not isinstance(output, str)
    ):
        return _protocol("Cursor success result envelope is malformed.")
    return DecodedOutput(output=encodable_text(output), error=document_error(value, "Cursor"))


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
