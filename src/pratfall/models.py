from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pratfall.codes import Code

Activity = Literal["starting", "working", "reasoning", "tool", "answering", "finishing"]


@dataclass(frozen=True)
class Capabilities:
    model: bool = True
    effort: bool = False
    effort_values: tuple[str, ...] = ()
    budgets: frozenset[str] = frozenset()
    fast: bool = False


@dataclass(frozen=True)
class AgentSpec:
    name: str
    label: str
    aliases: tuple[str, ...]
    command: tuple[str, ...]
    capabilities: Capabilities = Capabilities()
    version_args: tuple[str, ...] = ("--version",)


MAX_TURNS = 4_294_967_295


@dataclass(frozen=True)
class Options:
    model: str | None = None
    effort: str | None = None
    timeout: float | None = None
    max_budget_usd: float | None = None
    max_turns: int | None = None
    max_ai_credits: float | None = None
    fast: bool | None = None
    native_args: tuple[str, ...] | None = None


@dataclass(frozen=True)
class OptionOrigin:
    label: str
    code: Code = "invalid_config"


@dataclass(frozen=True)
class Profile:
    agent: str
    options: Options = Options()
    source: Path | None = None


@dataclass(frozen=True)
class PromptTemplate:
    prompt: str
    source: Path


@dataclass(frozen=True)
class Config:
    path: Path
    exists: bool = False
    defaults: Options = Options()
    commands: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    profiles: Mapping[str, Profile] = field(default_factory=lambda: MappingProxyType({}))
    sources: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
    default_sources: Mapping[str, Path] = field(default_factory=lambda: MappingProxyType({}))
    templates: Mapping[str, PromptTemplate] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ResolvedProfile:
    agent: AgentSpec
    profile: str | None
    command: tuple[str, ...]
    options: Options


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    stdin: bytes


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None


@dataclass(frozen=True)
class ResultError:
    code: Code
    message: str


@dataclass(frozen=True)
class DecodedOutput:
    output: str = ""
    usage: Usage | None = None
    error: ResultError | None = None
    timed_out: bool = False
    reported_models: tuple[str, ...] | None = None
    cost_usd: int | float | None = None


@dataclass(frozen=True)
class RawCapture:
    stdout: bytes = b""


@dataclass(frozen=True)
class ConsumedCapture:
    decoded: DecodedOutput


Capture = RawCapture | ConsumedCapture


@dataclass(frozen=True)
class NormalizedResult:
    agent: str | None
    profile: str | None
    model: str | None
    status: str
    output: str
    exit_code: int
    native_exit_code: int | None
    duration_ms: int
    usage: Usage | None
    error: ResultError | None
    reported_models: tuple[str, ...] | None
    cost_usd: int | float | None
