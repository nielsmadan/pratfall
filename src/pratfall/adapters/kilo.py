from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.opencode_stream import consumer as stream_consumer
from pratfall.adapters.opencode_stream import decode as decode_stream
from pratfall.consumer import DEFAULT_CONSUMER_LIMITS, ConsumerLimits, JsonlConsumer
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile

_ALLOWED = {name: Flag(0) for name in ("--auto", "--thinking", "--print-logs")} | {
    name: Flag(1, joined=name == "-f")
    for name in ("--agent", "--title", "--log-level", "--file", "-f")
}
_RESERVED = {
    name: Flag(0) for name in ("--continue", "-c", "--fork", "--interactive", "--share")
} | {
    name: Flag(1, joined=name in {"-m", "-s", "-f"})
    for name in (
        "--model",
        "-m",
        "--variant",
        "--format",
        "--attach",
        "--session",
        "-s",
        "--command",
        "--dir",
        "--port",
        "--hostname",
        "--password",
        "--username",
    )
}


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    argv = [*resolved.command, "run", "--format", "json"]
    if resolved.options.model is not None:
        argv.extend(("--model", resolved.options.model))
    if resolved.options.effort is not None:
        argv.extend(("--variant", resolved.options.effort))
    argv.extend(f"--file={attachment}" for attachment in resolved.options.attachments or ())
    argv.extend(resolved.options.native_args or ())
    return Invocation(tuple(argv), prompt, env=(("KILO_NO_DAEMON", "1"),))


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED | (
        {name: _ALLOWED[name] for name in ("--file", "-f")} if resolved.options.attachments else {}
    )
    validate_flags("Kilo", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    return decode_stream(stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return stream_consumer(limits)
