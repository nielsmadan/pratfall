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
            "--auto-accept-plans",
            "--bypass",
            "--dangerously-allow-all-tool-calls",
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
    | {
        name: Flag(0)
        for name in (
            "--allow-remote-control",
            "--auto-update",
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
            "--config-file",
            "--fork",
            "--goal",
            "--input-format",
            "--output-last-message",
            "--profile",
            "--resume-session-at",
            "--session-id",
            "--setting-sources",
            "--skills",
            "--worktree",
            "--worktree-base",
            "-o",
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
