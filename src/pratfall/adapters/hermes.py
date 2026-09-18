from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {
    name: Flag(0)
    for name in (
        "--accept-hooks",
        "--checkpoints",
        "--ignore-rules",
        "--ignore-user-config",
        "--pass-session-id",
        "--safe-mode",
        "--verbose",
        "--yolo",
        "-v",
    )
} | {
    name: Flag(1, joined=name in {"-s", "-t"})
    for name in (
        "--image",
        "--provider",
        "--run-budget",
        "--skills",
        "--source",
        "--toolsets",
        "-s",
        "-t",
    )
}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--cli",
        "--create-if-missing",
        "--dev",
        "--no-restore-cwd",
        "--oneshot",
        "--quiet",
        "--tui",
        "--worktree",
        "-Q",
        "-w",
    )
} | {
    name: Flag(1, joined=name in {"-m", "-q", "-r", "-z"})
    for name in (
        "--continue",
        "--in",
        "--max-turns",
        "--model",
        "--query",
        "--query-file",
        "--reasoning",
        "--resume",
        "-c",
        "-m",
        "-q",
        "-r",
        "-z",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "chat", "--oneshot", "--quiet", "--query-file", "-"]
    options = resolved.options
    if options.model is not None:
        argv.extend(("--model", options.model))
    if options.effort is not None:
        argv.extend(("--reasoning", options.effort))
    if options.max_turns is not None:
        argv.extend(("--max-turns", str(options.max_turns)))
    for attachment in resolved.options.attachments or ():
        argv.append(f"--image={attachment}")
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED | (
        {name: _ALLOWED[name] for name in ("--image",)} if resolved.options.attachments else {}
    )
    validate_flags("Hermes", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
