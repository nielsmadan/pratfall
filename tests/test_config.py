import os
from pathlib import Path

import pytest

from pratfall.catalog import BY_NAME
from pratfall.config import (
    DEFAULT_TIMEOUT,
    SAMPLE_CONFIG,
    config_path,
    init_config,
    load_config,
    merge_options,
    parse_options,
    resolve_profile,
    validate_capabilities,
)
from pratfall.errors import PratError
from pratfall.models import Options


def write_config(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(contents, encoding="utf-8")
    return path


def test_missing_default_resolves_builtin_defaults() -> None:
    config = load_config()
    resolved = resolve_profile(config, "cx")
    assert config.exists is False
    assert resolved.agent.name == "codex"
    assert resolved.profile is None
    assert resolved.command == ("codex",)
    assert resolved.options == Options(timeout=DEFAULT_TIMEOUT, native_args=())


@pytest.mark.parametrize("name", ["ki", "wp", "qw", "km", "mv"])
def test_removed_agent_aliases_are_available_for_profiles(tmp_path: Path, name: str) -> None:
    with pytest.raises(PratError, match="Unknown"):
        resolve_profile(load_config(), name)
    path = write_config(tmp_path, f'version=1\n[profiles.{name}]\nagent="codex"\n')
    resolved = resolve_profile(load_config(path), name)
    assert resolved.profile == name
    assert resolved.agent.name == "codex"


def test_explicit_missing_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"
    with pytest.raises(PratError, match=f"{path}: config file does not exist"):
        load_config(path)


@pytest.mark.parametrize("xdg", [None, ""])
def test_absent_or_empty_xdg_uses_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    xdg: str | None,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if xdg is None:
        monkeypatch.delenv("XDG_CONFIG_HOME")
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    assert config_path() == tmp_path / ".config/pratfall/config.toml"


def test_relative_paths_use_invocation_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "settings")
    assert config_path(cwd=tmp_path) == tmp_path / "settings/pratfall/config.toml"
    assert config_path("other.toml", cwd=tmp_path) == tmp_path / "other.toml"


def test_explicit_file_wins_over_default(tmp_path: Path) -> None:
    default = init_config()
    path = write_config(tmp_path, 'version=1\n[profiles.other]\nagent="gm"\n')
    assert list(load_config(path).profiles) == ["other"]
    assert list(load_config().profiles) == ["simple"]
    assert config_path() == default


def test_scalars_and_native_arguments_follow_precedence(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """version=1
[defaults]
model="default-model"
effort="low"
fast=true
timeout=90
native_args=["--verbose"]
[profiles.work]
agent="cc"
model="profile-model"
fast=false
timeout=30
max_budget_usd=2.5
max_turns=4
native_args=["--allowed-tools", "literal argument"]
""",
    )
    config = load_config(path)
    resolved = resolve_profile(config, "work", Options(model="cli-model", timeout=12))
    assert resolved.agent.name == "claude"
    assert resolved.profile == "work"
    assert resolved.options == Options(
        model="cli-model",
        effort="low",
        timeout=12,
        max_budget_usd=2.5,
        max_turns=4,
        fast=False,
        native_args=("--allowed-tools", "literal argument"),
    )
    assert resolve_profile(config, "cc").options.model == "default-model"
    assert resolve_profile(config, "work", Options(native_args=())).options.native_args == ()
    assert resolve_profile(
        config, "work", Options(native_args=("--strict-mcp-config",))
    ).options.native_args == ("--strict-mcp-config",)


def test_command_prefixes_resolve_paths_without_expanding_argument_data(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """version=1
[agents.codex]
command=["./bin/wrapper", "codex", "$TOKEN", "~/literal", "$(echo data)", "", "line\\nbreak"]
[agents.claude]
command=["claude-wrapper", "--config=other.toml"]
[agents.gemini]
command=["/absolute/gemini"]
[agents.hermes]
[profiles.work]
agent="codex"
native_args=["--profile", "$DATA", "--output-schema", "`literal`", "--add-dir", "~/file", "--sandbox", "two words"]
""",
    )
    config = load_config(path)
    resolved = resolve_profile(config, "work")
    assert resolved.command == (
        str(tmp_path / "bin/wrapper"),
        "codex",
        "$TOKEN",
        "~/literal",
        "$(echo data)",
        "",
        "line\nbreak",
    )
    assert resolved.options.native_args == (
        "--profile",
        "$DATA",
        "--output-schema",
        "`literal`",
        "--add-dir",
        "~/file",
        "--sandbox",
        "two words",
    )
    assert resolve_profile(config, "cc").command == ("claude-wrapper", "--config=other.toml")
    assert resolve_profile(config, "gm").command == ("/absolute/gemini",)
    assert resolve_profile(config, "hm").command == ("hermes",)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("", "version"),
        ("version=true", "version"),
        ("version=1.0", "version"),
        ("version=2", "version"),
        ("version=1\n[", "invalid TOML"),
        ("version=1\nunknown=true", "unknown field 'unknown'"),
        ("version=1\ndefaults=[]", "defaults: expected a TOML table"),
        ("version=1\nagents=[]", "agents: expected a TOML table"),
        ("version=1\nprofiles=[]", "profiles: expected a TOML table"),
        ('version=1\n[defaults]\nagent="codex"', "unknown field 'agent'"),
        ("version=1\n[defaults]\nmax_tokens=100", "unknown field 'max_tokens'"),
        ("version=1\n[defaults]\ntimeout=true", "defaults.timeout"),
        ("version=1\n[defaults]\ntimeout=0", "defaults.timeout"),
        ("version=1\n[defaults]\ntimeout=-1", "defaults.timeout"),
        ("version=1\n[defaults]\ntimeout=nan", "defaults.timeout"),
        ("version=1\n[defaults]\ntimeout=inf", "defaults.timeout"),
        ('version=1\n[defaults]\ntimeout="30"', "defaults.timeout"),
        ('version=1\n[defaults]\nmodel=" "', "defaults.model"),
        ('version=1\n[defaults]\neffort=""', "defaults.effort"),
        ('version=1\n[defaults]\nfast="true"', "defaults.fast"),
        ("version=1\n[defaults]\nfast=1", "defaults.fast"),
        ('version=1\n[defaults]\nnative_args="--flag"', "defaults.native_args"),
        ('version=1\n[defaults]\nnative_args=["--flag", 1]', "defaults.native_args"),
        ("version=1\n[defaults]\nmax_turns=1.5", "defaults.max_turns"),
        ("version=1\n[defaults]\nmax_turns=true", "defaults.max_turns"),
        ("version=1\n[defaults]\nmax_turns=0", "defaults.max_turns"),
        ("version=1\n[defaults]\nmax_budget_usd=nan", "defaults.max_budget_usd"),
        ("version=1\n[defaults]\nmax_ai_credits=-1", "defaults.max_ai_credits"),
        ("version=1\n[agents.cx]", "canonical name"),
        ("version=1\n[agents.unknown]", "canonical name"),
        ("version=1\n[agents]\ncodex=[]", "agents.codex: expected a TOML table"),
        ('version=1\n[agents.codex]\nmodel="other"', "unknown field 'model'"),
        ('version=1\n[agents.codex]\ncommand="codex"', "agents.codex.command"),
        ("version=1\n[agents.codex]\ncommand=[]", "nonempty executable"),
        ('version=1\n[agents.codex]\ncommand=[" "]', "nonempty executable"),
        ('version=1\n[profiles.cc]\nagent="codex"', "name is reserved"),
        ('version=1\n[profiles.config]\nagent="codex"', "name is reserved"),
        ('version=1\n[profiles."-bad"]\nagent="codex"', "profile names"),
        ('version=1\n[profiles."two words"]\nagent="codex"', "profile names"),
        ("version=1\n[profiles]\nwork=[]", "profiles.work: expected a TOML table"),
        ("version=1\n[profiles.work]", "profiles.work.agent"),
        ('version=1\n[profiles.work]\nagent="bad"', "profiles.work.agent: Unknown agent"),
        ('version=1\n[profiles.work]\nagent="codex"\ncommand=["bad"]', "unknown field 'command'"),
        (
            'version=1\n[profiles.work]\nagent="hermes"\neffort="unknown"',
            "Hermes accepts:",
        ),
        ('version=1\n[profiles.work]\nagent="kiro"\neffort="ultra"', "Kiro accepts:"),
        ('version=1\n[profiles.work]\nagent="claude"\neffort="bad"', "Claude Code accepts:"),
        (
            'version=1\n[profiles.work]\nagent="codex"\nmax_budget_usd=1',
            "profiles.work.max_budget_usd",
        ),
        (
            'version=1\n[profiles.work]\nagent="claude"\nmax_ai_credits=1',
            "profiles.work.max_ai_credits",
        ),
        ('version=1\n[profiles.work]\nagent="codex"\nmax_turns=1', "profiles.work.max_turns"),
        ('version=1\n[profiles.work]\nagent="gemini"\nfast=false', "profiles.work.fast"),
    ],
)
def test_invalid_config_identifies_source_and_field(
    tmp_path: Path, contents: str, message: str
) -> None:
    path = write_config(tmp_path, contents)
    with pytest.raises(PratError, match=message) as caught:
        load_config(path)
    assert str(caught.value).startswith(str(path))
    assert caught.value.exit_code == 2


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"model": "a\0b"}, "model"),
        ({"effort": False}, "effort"),
        ({"native_args": ["a\0b"]}, "native_args"),
        ({"timeout": 10**1000}, "timeout"),
    ],
)
def test_invalid_programmatic_values_fail(options: dict[str, object], message: str) -> None:
    with pytest.raises(PratError, match=message):
        parse_options(options, "test")


def test_arbitrary_native_model_and_effort_values_are_preserved() -> None:
    options = Options(model="provider/future-model", effort="future-effort")
    validate_capabilities(BY_NAME["codex"], options, "test")
    assert merge_options(Options(model="first"), options) == options


def test_supported_native_budgets_are_resolved(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """version=1
[profiles.credit]
agent="copilot"
max_ai_credits=2.5
[profiles.turns]
agent="hermes"
max_turns=3
""",
    )
    config = load_config(path)
    assert resolve_profile(config, "credit").options.max_ai_credits == 2.5
    assert resolve_profile(config, "turns").options.max_turns == 3


def test_defaults_and_overrides_are_validated_for_selected_builtin(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'version=1\n[defaults]\nmodel="new-model"\n')
    config = load_config(path)
    assert resolve_profile(config, "kiro").options.model == "new-model"
    with pytest.raises(PratError, match="Hermes accepts:"):
        resolve_profile(config, "hm", Options(effort="unknown"))


def test_fast_mode_follows_cli_profile_defaults_precedence(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        'version=1\n[defaults]\nfast=true\n[profiles.work]\nagent="codex"\nfast=false\n',
    )
    config = load_config(path)
    assert resolve_profile(config, "cx").options.fast is True
    assert resolve_profile(config, "work").options.fast is False
    assert resolve_profile(config, "work", Options(fast=True)).options.fast is True


def test_global_fast_is_checked_only_when_a_builtin_is_resolved(tmp_path: Path) -> None:
    path = write_config(tmp_path, "version=1\n[defaults]\nfast=true\n")
    config = load_config(path)
    assert resolve_profile(config, "cc").options.fast is True
    with pytest.raises(PratError, match="Gemini does not support"):
        resolve_profile(config, "gm")


def test_unknown_selector_suggests_inventory() -> None:
    with pytest.raises(PratError, match="prat profiles") as caught:
        resolve_profile(load_config(), "unknown")
    assert caught.value.code == "invalid_arguments"


def test_config_init_creates_valid_sample_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "nested/config.toml"
    assert init_config(path) == path
    assert path.read_text(encoding="utf-8") == SAMPLE_CONFIG
    resolved = resolve_profile(load_config(path), "simple")
    assert resolved.agent.name == "codex"
    assert resolved.options.model == "gpt-5.6-luna"
    assert resolved.options.effort == "low"
    original = path.read_bytes()
    with pytest.raises(PratError, match="already exists"):
        init_config(path)
    assert path.read_bytes() == original


def test_config_init_secures_only_new_paths_under_open_umask(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o751)
    existing.chmod(0o751)
    path = existing / "new" / "nested" / "config.toml"
    previous_umask = os.umask(0)
    try:
        init_config(path)
    finally:
        os.umask(previous_umask)
    assert existing.stat().st_mode & 0o777 == 0o751
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_config_init_preserves_existing_file_mode_and_contents(tmp_path: Path) -> None:
    path = write_config(tmp_path, "preserve this")
    path.chmod(0o640)
    with pytest.raises(PratError, match="already exists"):
        init_config(path)
    assert path.read_text(encoding="utf-8") == "preserve this"
    assert path.stat().st_mode & 0o777 == 0o640


def test_init_refuses_existing_symlink(tmp_path: Path) -> None:
    target = write_config(tmp_path, "preserve this")
    link = tmp_path / "link.toml"
    link.symlink_to(target)
    with pytest.raises(PratError, match="already exists"):
        init_config(link)
    assert target.read_text(encoding="utf-8") == "preserve this"


def test_file_read_errors_have_config_context(tmp_path: Path) -> None:
    with pytest.raises(PratError, match="cannot read config"):
        load_config(tmp_path)
    path = tmp_path / "invalid.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(PratError, match="cannot read config"):
        load_config(path)


def test_init_reports_parent_creation_failure(tmp_path: Path) -> None:
    file = write_config(tmp_path, "existing file")
    with pytest.raises(PratError, match="cannot create config"):
        init_config(file / "config.toml")


@pytest.mark.parametrize(
    "name",
    [
        "openhands",
        "oh",
        "warp",
        "iflow",
        "if",
        "qwen",
        "amp",
        "reasonix",
        "rx",
        "droid",
        "dr",
        "kimi",
        "vibe",
        "crush",
        "cr",
        "devin",
        "dv",
        "cortex",
        "co",
    ],
)
def test_new_agent_profile_collisions_have_exact_migration_diagnostic(
    tmp_path: Path, name: str
) -> None:
    path = write_config(tmp_path, f'version=1\n[profiles.{name}]\nagent="codex"\n')
    with pytest.raises(PratError) as failure:
        load_config(path)
    assert str(failure.value) == (
        f"{path}: profiles.{name}: name is reserved; choose a different profile name."
    )
