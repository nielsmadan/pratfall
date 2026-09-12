from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {name: Flag(1) for name in ("--connection", "-c")}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--continue",
        "--private",
        "--plan",
        "--auto-accept-plans",
        "--bypass",
        "--dangerously-allow-all-tool-calls",
        "--no-workspace",
        "--help",
        "--version",
        "-V",
    )
} | {
    name: Flag(1, joined=name in {"-m", "-w", "-r", "-p", "-f"})
    for name in (
        "--model",
        "-m",
        "--effort",
        "--max-turns",
        "--workdir",
        "-w",
        "--cwd",
        "--config",
        "--resume",
        "-r",
        "--session",
        "--print",
        "-p",
        "--prompt",
        "--file",
        "-f",
        "--output-format",
        "--cloud",
        "--github",
        "--executor",
        "--remote",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    validate(arguments)
    argv = [*resolved.command]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--effort", resolved.options.effort))
    if resolved.options.max_turns is not None:
        argv.extend(("--max-turns", str(resolved.options.max_turns)))
    argv.extend((*arguments, "exec", "--file", "-"))
    return Invocation(tuple(argv), prompt)


def validate(arguments: tuple[str, ...]) -> None:
    validate_flags("Cortex Code", arguments, _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
