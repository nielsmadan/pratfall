from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
    grok,
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
from pratfall.consumer import ConsumerFactory, decode_with
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile


def _whole_document(factory: ConsumerFactory) -> Callable[[str], DecodedOutput]:
    def decoder(stdout: str) -> DecodedOutput:
        return decode_with(factory, stdout)

    return decoder


@dataclass(frozen=True)
class Adapter:
    build: Callable[[ResolvedProfile, bytes], Invocation]
    validate: Callable[[ResolvedProfile], None]
    whole_document: Callable[[str], DecodedOutput] | None = None
    consumer: ConsumerFactory | None = None
    decode: Callable[[str], DecodedOutput] = field(init=False)

    def __post_init__(self) -> None:
        if self.whole_document is not None and self.consumer is None:
            object.__setattr__(self, "decode", self.whole_document)
        elif self.consumer is not None and self.whole_document is None:
            object.__setattr__(self, "decode", _whole_document(self.consumer))
        else:
            raise TypeError("Adapter needs exactly one of whole_document or consumer.")


ADAPTERS: Mapping[str, Adapter] = MappingProxyType(
    {
        "claude": Adapter(
            build=claude.build,
            validate=claude.validate,
            whole_document=claude.decode,
        ),
        "codex": Adapter(
            build=codex.build,
            validate=codex.validate,
            consumer=codex.consumer,
        ),
        "gemini": Adapter(
            build=gemini.build,
            validate=gemini.validate,
            whole_document=gemini.decode,
        ),
        "antigravity": Adapter(
            build=antigravity.build,
            validate=antigravity.validate,
            consumer=antigravity.consumer,
        ),
        "copilot": Adapter(
            build=copilot.build,
            validate=copilot.validate,
            consumer=copilot.consumer,
        ),
        "kiro": Adapter(
            build=kiro.build,
            validate=kiro.validate,
            whole_document=kiro.decode,
        ),
        "cursor": Adapter(
            build=cursor.build,
            validate=cursor.validate,
            whole_document=cursor.decode,
        ),
        "openclaw": Adapter(
            build=openclaw.build,
            validate=openclaw.validate,
            whole_document=openclaw.decode,
        ),
        "hermes": Adapter(
            build=hermes.build,
            validate=hermes.validate,
            whole_document=hermes.decode,
        ),
        "opencode": Adapter(
            build=opencode.build,
            validate=opencode.validate,
            consumer=opencode.consumer,
        ),
        "openhands": Adapter(
            build=openhands.build,
            validate=openhands.validate,
            consumer=openhands.consumer,
        ),
        "warp": Adapter(
            build=warp.build,
            validate=warp.validate,
            consumer=warp.consumer,
        ),
        "qwen": Adapter(build=qwen.build, validate=qwen.validate, consumer=qwen.consumer),
        "amp": Adapter(build=amp.build, validate=amp.validate, consumer=amp.consumer),
        "reasonix": Adapter(
            build=reasonix.build, validate=reasonix.validate, whole_document=reasonix.decode
        ),
        "droid": Adapter(build=droid.build, validate=droid.validate, whole_document=droid.decode),
        "kimi": Adapter(build=kimi.build, validate=kimi.validate, consumer=kimi.consumer),
        "vibe": Adapter(build=vibe.build, validate=vibe.validate, whole_document=vibe.decode),
        "crush": Adapter(build=crush.build, validate=crush.validate, whole_document=crush.decode),
        "devin": Adapter(build=devin.build, validate=devin.validate, whole_document=devin.decode),
        "cortex": Adapter(
            build=cortex.build, validate=cortex.validate, whole_document=cortex.decode
        ),
        "grok": Adapter(build=grok.build, validate=grok.validate, whole_document=grok.decode),
        "iflow": Adapter(
            build=iflow.build,
            validate=iflow.validate,
            whole_document=iflow.decode,
        ),
    }
)
