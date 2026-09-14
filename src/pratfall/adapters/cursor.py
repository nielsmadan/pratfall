import json

from pratfall.adapters.native_args import Flag, validate_flags
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
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as error:
        return _protocol(f"Invalid Cursor JSON: {error.msg}.")
    except ValueError:
        return _protocol("Invalid Cursor JSON: numeric value is too large.")
    except RecursionError:
        return _protocol("Invalid Cursor JSON: document nesting is too deep.")
    if not isinstance(value, dict):
        return _protocol("Cursor result must be a JSON object.")
    if (
        value.get("type") != "result"
        or value.get("subtype") != "success"
        or value.get("is_error") is not False
        or not isinstance(value.get("result"), str)
    ):
        return _protocol("Cursor success result envelope is malformed.")
    return DecodedOutput(output=value["result"])


def _protocol(message: str) -> DecodedOutput:
    return DecodedOutput(error=ResultError("protocol_error", message))
