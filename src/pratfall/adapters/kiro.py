from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--require-mcp-startup",
        "--trust-all-tools",
        "--verbose",
        "-v",
        "-vv",
        "-vvv",
    )
} | {name: Flag(1) for name in ("--agent", "--trust-tools")}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--list-models",
        "--list-sessions",
        "--no-interactive",
        "--resume",
        "--resume-picker",
        "-r",
    )
} | {
    name: Flag(1)
    for name in (
        "--delete-session",
        "--effort",
        "--engine",
        "--model",
        "--output-format",
        "--resume-id",
        "--wrap",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "chat", "--no-interactive", "--wrap", "never"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--effort", resolved.options.effort))
    argv.extend(arguments)
    argv.extend(("--", prompt.decode("utf-8")))
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Kiro", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
