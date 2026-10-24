import re
from collections.abc import Mapping
from types import MappingProxyType

from pratfall.adapters.native_args import Flag, validate_flags
from pratfall.adapters.opencode_stream import consumer as stream_consumer
from pratfall.adapters.opencode_stream import decode as decode_stream
from pratfall.adapters.opencode_stream import unfinished_final_step_consumer
from pratfall.consumer import DEFAULT_CONSUMER_LIMITS, ConsumerLimits, JsonlConsumer
from pratfall.errors import PratError
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
        for name in (
            "--continue",
            "-c",
            "--fork",
            "--interactive",
            "-i",
            "--share",
            "--standalone",
        )
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
            "--server",
            "--session",
            "-u",
            "--username",
            "--variant",
        )
    }
)


_V2_ALLOWED: Mapping[str, Flag] = MappingProxyType(
    {name: flag for name, flag in _ALLOWED.items() if name not in {"--pure", "--log-level"}}
)

_VERSION = re.compile(r"(?<![\d.])(\d+)\.\d+")
V2_MAJOR = 2


def major_version(version: str) -> int:
    match = _VERSION.search(version)
    if match is None:
        raise PratError(
            f"OpenCode version output is not recognized: {version!r}.", code="protocol_error"
        )
    return int(match.group(1))


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    options = resolved.options
    model = () if options.model is None else ("--model", options.model)
    effort = () if options.effort is None else ("--variant", options.effort)
    return _invocation(resolved, prompt, ("run", "--format", "json", *model, *effort))


def build_v2(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    model, effort = resolved.options.model, resolved.options.effort
    selected = (
        () if model is None else ("--model", model if effort is None else f"{model}#{effort}")
    )
    return _invocation(resolved, prompt, ("run", "--standalone", "--format", "json", *selected))


def _invocation(resolved: ResolvedProfile, prompt: bytes, head: tuple[str, ...]) -> Invocation:
    attachments = (f"--file={attachment}" for attachment in resolved.options.attachments or ())
    return Invocation(
        (*resolved.command, *head, *attachments, *(resolved.options.native_args or ())), prompt
    )


def validate(resolved: ResolvedProfile) -> None:
    _validate_flags(resolved, _ALLOWED)


def validate_v2(resolved: ResolvedProfile) -> None:
    _validate_flags(resolved, _V2_ALLOWED)
    model, effort = resolved.options.model, resolved.options.effort
    if effort is None:
        return
    if model is None:
        raise PratError(
            "OpenCode 2 selects effort as a model variant; set a model as well.",
            code="invalid_arguments",
            option="effort",
        )
    if "#" in model:
        raise PratError(
            "OpenCode 2 model already names a #variant; remove it or the effort.",
            code="invalid_arguments",
            option="effort",
        )


def _validate_flags(resolved: ResolvedProfile, allowed: Mapping[str, Flag]) -> None:
    reserved = dict(_RESERVED) | (
        {name: allowed[name] for name in ("--file", "-f")} if resolved.options.attachments else {}
    )
    validate_flags("OpenCode", resolved.options.native_args or (), allowed, reserved)


def decode(stdout: str) -> DecodedOutput:
    return decode_stream(stdout)


def consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return stream_consumer(limits)


def v2_consumer(limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> JsonlConsumer:
    return unfinished_final_step_consumer(limits)
