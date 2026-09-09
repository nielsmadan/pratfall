from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pratfall.adapters import antigravity, claude, codex, copilot, cursor, gemini, opencode
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile


@dataclass(frozen=True)
class Adapter:
    build: Callable[[ResolvedProfile, bytes], Invocation]
    decode: Callable[[str], DecodedOutput]
    validate: Callable[[tuple[str, ...]], None]


ADAPTERS: Mapping[str, Adapter] = MappingProxyType(
    {
        name: Adapter(module.build, module.decode, module.validate)
        for name, module in (
            ("claude", claude),
            ("codex", codex),
            ("gemini", gemini),
            ("antigravity", antigravity),
            ("copilot", copilot),
            ("cursor", cursor),
            ("opencode", opencode),
        )
    }
)
