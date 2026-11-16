from collections.abc import Mapping
from types import MappingProxyType

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED: Mapping[str, Flag] = MappingProxyType({name: Flag(1) for name in ("--connection", "-c")})
_RESERVED: Mapping[str, Flag] = MappingProxyType(
    {
        name: Flag(0)
        for name in (
            "--continue",
            "--private",
            "--plan",
            "--no-workspace",
            "--help",
            "--version",
            "-V",
        )
    }
    | {
        name: Flag(1, joined=name in {"-m", "-w", "-r", "-p", "-f"})
        for name in (
            "--model",
            "-m",
            "--effort",
            "--max-turns",
            "--workdir",
            "-w",
            "--cwd",
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
    | {
        name: Flag(0)
        for name in (
            "--allow-remote-control",
            "--background-agent",
            "--desktop-sidecar",
            "--fips-status",
            "--fork-session",
            "--include-partial-messages",
            "--server",
            "--snowflake-sandbox",
            "--swarm",
            "--ui",
        )
    }
    | {
        name: Flag(1, joined=name in {"-o"})
        for name in (
            "--channels",
            "--fork",
            "--goal",
            "--input-format",
            "--resume-session-at",
            "--session-id",
            "--worktree",
            "--worktree-base",
        )
    }
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--effort", resolved.options.effort))
    if resolved.options.max_turns is not None:
        argv.extend(("--max-turns", str(resolved.options.max_turns)))
    argv.extend((*arguments, "exec", "--file", "-"))
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags(
        "Cortex Code",
        resolved.options.native_args or (),
        _ALLOWED,
        _RESERVED,
        attached_short_values=False,
    )


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
