from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class Capabilities:
    model: bool = True
    effort: bool = False
    effort_values: tuple[str, ...] = ()
    budgets: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgentSpec:
    name: str
    label: str
    aliases: tuple[str, ...]
    command: tuple[str, ...]
    capabilities: Capabilities = Capabilities()


@dataclass(frozen=True)
class Options:
    model: str | None = None
    effort: str | None = None
    timeout: float | None = None
    max_budget_usd: float | None = None
    max_turns: int | None = None
    max_ai_credits: float | None = None
    native_args: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Profile:
    agent: str
    options: Options = Options()


@dataclass(frozen=True)
class Config:
    path: Path
    exists: bool = False
    defaults: Options = Options()
    commands: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    profiles: Mapping[str, Profile] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ResolvedProfile:
    agent: AgentSpec
    profile: str | None
    command: tuple[str, ...]
    options: Options
