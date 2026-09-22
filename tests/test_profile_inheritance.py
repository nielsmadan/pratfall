from pathlib import Path

import pytest

from pratfall.config import init_config, load_config, option_origins, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options

BASES = (
    "version=1\n"
    '[profiles.claude-base]\nagent="claude"\nmodel="claude-sonnet-5"\n'
    'tools=["Read","Edit","Bash"]\ntimeout=1800\n'
    '[profiles.codex-base]\nagent="codex"\neffort="medium"\n'
)


def _local(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / ".pratfile"
    path.write_text(contents, encoding="utf-8")
    return path


def test_a_local_child_inherits_a_global_base_across_files(tmp_path: Path) -> None:
    global_path = init_config()
    global_path.write_text(BASES, encoding="utf-8")
    _local(tmp_path, 'version=1\n[profiles.weekly]\nextends="claude-base"\nmodel="claude-opus-5"\n')

    resolved = resolve_profile(load_config(), "weekly")

    assert resolved.agent.name == "claude"
    assert resolved.options == Options(
        model="claude-opus-5", timeout=1800, tools=("Read", "Edit", "Bash"), native_args=()
    )


def test_a_base_for_another_agent_no_longer_poisons_the_file(tmp_path: Path) -> None:
    """The regression this feature exists for: agent-specific settings live on the
    agent's base profile, so a codex profile in the same file cannot break claude."""
    global_path = init_config()
    global_path.write_text(BASES, encoding="utf-8")
    _local(tmp_path, 'version=1\n[profiles.weekly]\nextends="claude-base"\n')

    config = load_config()

    assert resolve_profile(config, "weekly").options.tools == ("Read", "Edit", "Bash")
    assert resolve_profile(config, "codex-base").agent.name == "codex"


def test_a_chain_resolves_nearest_ancestor_first(tmp_path: Path) -> None:
    _local(
        tmp_path,
        "version=1\n"
        '[profiles.root]\nagent="claude"\nmodel="root-model"\neffort="low"\ntimeout=10\n'
        '[profiles.middle]\nextends="root"\neffort="high"\ntimeout=20\n'
        '[profiles.leaf]\nextends="middle"\ntimeout=30\n',
    )

    resolved = resolve_profile(load_config(), "leaf")

    assert resolved.agent.name == "claude"
    assert resolved.options == Options(
        model="root-model", effort="high", timeout=30, native_args=()
    )


def test_an_inherited_array_is_replaced_not_merged(tmp_path: Path) -> None:
    _local(
        tmp_path,
        "version=1\n"
        '[profiles.base]\nagent="claude"\ntools=["Read","Edit","Bash"]\n'
        '[profiles.child]\nextends="base"\ntools=["Skill"]\n',
    )

    assert resolve_profile(load_config(), "child").options.tools == ("Skill",)


def test_an_inherited_value_beats_a_local_default(tmp_path: Path) -> None:
    _local(
        tmp_path,
        "version=1\n[defaults]\ntimeout=60\n"
        '[profiles.base]\nagent="claude"\ntimeout=1800\n'
        '[profiles.child]\nextends="base"\n'
        '[profiles.plain]\nagent="claude"\n',
    )
    config = load_config()

    assert resolve_profile(config, "child").options.timeout == 1800
    assert resolve_profile(config, "plain").options.timeout == 60


def test_provenance_names_the_profile_that_defined_the_field(tmp_path: Path) -> None:
    global_path = init_config()
    global_path.write_text(BASES, encoding="utf-8")
    local = _local(
        tmp_path, 'version=1\n[profiles.weekly]\nextends="claude-base"\nmodel="claude-opus-5"\n'
    )

    origins = option_origins(load_config(), "weekly")

    assert (
        origins["tools"].label == f"{global_path}: profiles.claude-base.tools (selector 'weekly')"
    )
    assert origins["model"].label == f"{local}: profiles.weekly.model"


def test_an_inherited_option_the_child_agent_rejects_names_the_parent(tmp_path: Path) -> None:
    global_path = init_config()
    global_path.write_text(BASES, encoding="utf-8")
    _local(tmp_path, 'version=1\n[profiles.odd]\nextends="claude-base"\nagent="codex"\n')

    with pytest.raises(PratError) as caught:
        load_config()

    assert caught.value.args[0] == (
        f"{global_path}: profiles.claude-base.tools (selector 'odd'): "
        "Codex does not support tool allowlists."
    )
    assert caught.value.code == "invalid_config"


def test_a_root_profile_still_requires_an_agent(tmp_path: Path) -> None:
    path = _local(tmp_path, 'version=1\n[profiles.work]\nmodel="m"\n')

    with pytest.raises(PratError) as caught:
        load_config()

    assert caught.value.args[0] == (
        f"{path}: profiles.work.agent: expected a nonempty string without NUL bytes."
    )


def test_a_child_may_override_the_inherited_agent(tmp_path: Path) -> None:
    global_path = init_config()
    global_path.write_text(BASES, encoding="utf-8")
    _local(tmp_path, 'version=1\n[profiles.swap]\nextends="codex-base"\nagent="claude"\n')

    resolved = resolve_profile(load_config(), "swap")

    assert resolved.agent.name == "claude"
    assert resolved.options.effort == "medium"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[profiles.work]\nextends=1\n", "profiles.work.extends: expected"),
        (
            '[profiles.work]\nagent="claude"\nextends="missing"\n',
            "profiles.work.extends: unknown profile 'missing'.",
        ),
        (
            '[profiles.work]\nextends="work"\n',
            "profiles.work.extends: inheritance cycle: work -> work.",
        ),
        (
            '[profiles.a]\nextends="b"\n[profiles.b]\nextends="a"\n',
            "inheritance cycle: a -> b -> a.",
        ),
        ('[profiles.work]\nextends="claude"\n', "profiles.work.extends: unknown profile 'claude'."),
        ('[defaults]\nextends="x"\n', "unknown field 'extends'"),
    ],
)
def test_extends_errors_name_their_source_and_field(
    tmp_path: Path, body: str, message: str
) -> None:
    path = _local(tmp_path, "version=1\n" + body)

    with pytest.raises(PratError) as caught:
        load_config()

    assert str(path) in caught.value.args[0]
    assert message in caught.value.args[0]
    assert caught.value.code == "invalid_config"


def test_a_local_base_replaces_the_global_one_for_every_child(tmp_path: Path) -> None:
    global_path = init_config()
    global_path.write_text(
        'version=1\n[profiles.base]\nagent="claude"\nmodel="global"\n'
        '[profiles.from-global]\nextends="base"\n',
        encoding="utf-8",
    )
    _local(
        tmp_path,
        'version=1\n[profiles.base]\nagent="claude"\nmodel="local"\n'
        '[profiles.from-local]\nextends="base"\n',
    )
    config = load_config()

    assert resolve_profile(config, "from-global").options.model == "local"
    assert resolve_profile(config, "from-local").options.model == "local"
    assert config.warnings == (
        f"{tmp_path / '.pratfile'}: profile 'base' replaces the profile from {global_path}.",
    )
