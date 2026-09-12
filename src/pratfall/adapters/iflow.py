from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {name: Flag(0) for name in ("--thinking", "--plan", "--default")}
_RESERVED = {name: Flag(0) for name in ("--continue", "-c", "--stream", "--experimental-acp")} | {
    name: Flag(1, joined=name in {"-p", "-i", "-m", "-r", "-o"})
    for name in (
        "--prompt",
        "-p",
        "--prompt-interactive",
        "-i",
        "--model",
        "-m",
        "--resume",
        "-r",
        "--output-file",
        "--output_file",
        "-o",
        "--output-format",
        "--input-format",
        "--max-turns",
        "--max_turns",
        "--max-tokens",
        "--max_tokens",
        "--timeout",
        "--port",
        "--cwd",
        "--config",
        "--file",
        "--session",
        "--executor",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    argv.extend(arguments)
    argv.append(f"--prompt={prompt.decode('utf-8')}")
    return Invocation(tuple(argv), b"")


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("iFlow", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
