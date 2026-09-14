from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {"--permission-mode": Flag(1)}
_RESERVED = {
    name: Flag(0) for name in ("--print", "-p", "--continue", "-c", "--help", "--version")
} | {
    name: Flag(1, joined=name == "-r")
    for name in (
        "--model",
        "--prompt",
        "--prompt-file",
        "--file",
        "--output-format",
        "--export",
        "--config",
        "--cwd",
        "--resume",
        "-r",
        "--respect-workspace-trust",
        "--executor",
        "--remote",
        "--cloud",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "-p"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend((*arguments, "--", prompt.decode("utf-8")))
    return Invocation(tuple(argv), b"")


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Devin", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
