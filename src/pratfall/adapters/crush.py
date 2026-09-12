from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {name: Flag(0) for name in ("--verbose", "-v", "--debug", "-d")}
_RESERVED = {
    name: Flag(0)
    for name in ("--quiet", "-q", "--continue", "-C", "--yolo", "-y", "--help", "--version")
} | {
    name: Flag(1, joined=name in {"-m", "-s", "-c", "-D", "-H"})
    for name in (
        "--model",
        "-m",
        "--small-model",
        "--session",
        "-s",
        "--cwd",
        "-c",
        "--data-dir",
        "-D",
        "--host",
        "-H",
        "--channels",
        "--config",
        "--prompt",
        "--file",
        "--output-format",
        "--executor",
        "--remote",
        "--cloud",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command, "run", "--quiet"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Crush", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
