from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pratfall.adapters import (
    antigravity,
    claude,
    codex,
    copilot,
    cursor,
    gemini,
    hermes,
    kiro,
    openclaw,
    opencode,
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
        name: Adapter(
            module.build,
            module.decode,
            module.validate,
            openclaw.validate_resolved if name == "openclaw" else None,
            module.consumer if name in {"codex", "copilot", "antigravity", "opencode"} else None,
        )
        for name, module in (
            ("claude", claude),
            ("codex", codex),
            ("gemini", gemini),
            ("antigravity", antigravity),
            ("copilot", copilot),
            ("kiro", kiro),
            ("cursor", cursor),
            ("openclaw", openclaw),
            ("hermes", hermes),
            ("opencode", opencode),
        )
    }
)
