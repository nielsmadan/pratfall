from collections.abc import Mapping
from types import MappingProxyType

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.opencode_stream import consumer as stream_consumer
from pratfall.adapters.opencode_stream import decode as decode_stream
from pratfall.consumer import DEFAULT_CONSUMER_LIMITS, ConsumerLimits, JsonlConsumer
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED: Mapping[str, Flag] = MappingProxyType(
    {
        name: Flag(0)
        for name in (
            "--auto",
            "--dangerously-skip-permissions",
            "--print-logs",
            "--pure",
            "--thinking",
            "--yolo",
        )
    }
    | {
        name: Flag(1, joined=name == "-f")
        for name in ("--agent", "--file", "-f", "--log-level", "--title")
    }
)
_RESERVED: Mapping[str, Flag] = MappingProxyType(
    {
        name: Flag(0, joined=name in {"-c", "-i"})
        for name in ("--continue", "-c", "--fork", "--interactive", "-i", "--share")
    }
    | {
        name: Flag(1, joined=name in {"-m", "-p", "-s", "-u"})
        for name in (
            "--attach",
            "--command",
            "--dir",
            "--format",
            "-m",
            "--model",
            "-p",
            "--password",
            "--port",
            "-s",
            "--session",
            "-u",
            "--username",
            "--variant",
        )
    }
)


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "run", "--format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--variant", resolved.options.effort))
    argv.extend(f"--file={attachment}" for attachment in resolved.options.attachments or ())
    argv.extend(arguments)
    return Invocation(tuple(argv), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = dict(_RESERVED) | (
        {name: _ALLOWED[name] for name in ("--file", "-f")} if resolved.options.attachments else {}
    )
    validate_flags("OpenCode", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    return decode_stream(stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return stream_consumer(limits)
