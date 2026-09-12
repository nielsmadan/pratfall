from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pratfall.adapters import (
    amp,
    antigravity,
    claude,
    codex,
    copilot,
    cortex,
    crush,
    cursor,
    devin,
    droid,
    gemini,
    hermes,
    iflow,
    kimi,
    kiro,
    openclaw,
    opencode,
    openhands,
    qwen,
    reasonix,
    vibe,
    warp,
)
from pratfall.consumer import ByteConsumer
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile


@dataclass(frozen=True)
class Adapter:
    build: Callable[[ResolvedProfile, bytes], Invocation]
    decode: Callable[[str], DecodedOutput]
    validate: Callable[[tuple[str, ...]], None]
    validate_resolved: Callable[[ResolvedProfile], None] | None = None
    consumer: Callable[[], ByteConsumer] | None = None


ADAPTERS: Mapping[str, Adapter] = MappingProxyType(
    {
        "claude": Adapter(
            build=claude.build,
            decode=claude.decode,
            validate=claude.validate,
        ),
        "codex": Adapter(
            build=codex.build,
            decode=codex.decode,
            validate=codex.validate,
            consumer=codex.consumer,
        ),
        "gemini": Adapter(
            build=gemini.build,
            decode=gemini.decode,
            validate=gemini.validate,
        ),
        "antigravity": Adapter(
            build=antigravity.build,
            decode=antigravity.decode,
            validate=antigravity.validate,
            consumer=antigravity.consumer,
        ),
        "copilot": Adapter(
            build=copilot.build,
            decode=copilot.decode,
            validate=copilot.validate,
            consumer=copilot.consumer,
        ),
        "kiro": Adapter(
            build=kiro.build,
            decode=kiro.decode,
            validate=kiro.validate,
        ),
        "cursor": Adapter(
            build=cursor.build,
            decode=cursor.decode,
            validate=cursor.validate,
        ),
        "openclaw": Adapter(
            build=openclaw.build,
            decode=openclaw.decode,
            validate=openclaw.validate,
            validate_resolved=openclaw.validate_resolved,
        ),
        "hermes": Adapter(
            build=hermes.build,
            decode=hermes.decode,
            validate=hermes.validate,
        ),
        "opencode": Adapter(
            build=opencode.build,
            decode=opencode.decode,
            validate=opencode.validate,
            consumer=opencode.consumer,
        ),
        "openhands": Adapter(
            build=openhands.build,
            decode=openhands.decode,
            validate=openhands.validate,
            consumer=openhands.consumer,
        ),
        "warp": Adapter(
            build=warp.build,
            decode=warp.decode,
            validate=warp.validate,
            consumer=warp.consumer,
        ),
        "qwen": Adapter(
            build=qwen.build, decode=qwen.decode, validate=qwen.validate, consumer=qwen.consumer
        ),
        "amp": Adapter(
            build=amp.build, decode=amp.decode, validate=amp.validate, consumer=amp.consumer
        ),
        "reasonix": Adapter(
            build=reasonix.build, decode=reasonix.decode, validate=reasonix.validate
        ),
        "droid": Adapter(build=droid.build, decode=droid.decode, validate=droid.validate),
        "kimi": Adapter(
            build=kimi.build, decode=kimi.decode, validate=kimi.validate, consumer=kimi.consumer
        ),
        "vibe": Adapter(build=vibe.build, decode=vibe.decode, validate=vibe.validate),
        "crush": Adapter(build=crush.build, decode=crush.decode, validate=crush.validate),
        "devin": Adapter(build=devin.build, decode=devin.decode, validate=devin.validate),
        "cortex": Adapter(build=cortex.build, decode=cortex.decode, validate=cortex.validate),
        "iflow": Adapter(
            build=iflow.build,
            decode=iflow.decode,
            validate=iflow.validate,
        ),
    }
)
