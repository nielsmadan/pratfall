from collections.abc import Mapping
from types import MappingProxyType

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED: Mapping[str, Flag] = MappingProxyType(
    {name: Flag(0) for name in ("--verbose", "-v", "--debug", "-d")}
)
_RESERVED: Mapping[str, Flag] = MappingProxyType(
    {
        name: Flag(0)
        for name in ("--quiet", "-q", "--continue", "-C", "--yolo", "-y", "--help", "--version")
    }
    | {
        name: Flag(1, joined=name in {"-m", "-s", "-c", "-D", "-H"})
        for name in (
            "--model",
            "-m",
            "--small-model",
            "--reasoning-effort",
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
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "run", "--quiet"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--reasoning-effort", resolved.options.effort))
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    validate_flags("Crush", resolved.options.native_args or (), _ALLOWED, _RESERVED)


def decode(stdout: str) -> DecodedOutput:
    return DecodedOutput(output=stdout.rstrip("\r\n"))
