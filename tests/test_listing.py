import os
from pathlib import Path

import pytest

from pratfall.catalog import AGENTS
from pratfall.cli.listing import list_view, profile_page
from pratfall.cli.presentation import display_width, render_table
from pratfall.config import load_config
from pratfall.models import Config, PromptTemplate

NONE_INSTALLED = ", ".join(agent.name for agent in AGENTS)


def nothing_installed(_executable: str) -> None:
    return None


def test_table_columns_are_as_wide_as_their_longest_cell() -> None:
    assert render_table(
        [("NAME", "AGENT", "FROM"), ("weekly", "claude", "global"), ("b", "", "x")]
    ) == [
        "NAME    AGENT   FROM",
        "weekly  claude  global",
        "b       -       x",
    ]


def test_table_counts_wide_characters_as_two_cells() -> None:
    lines = render_table([("NAME", "FROM"), ("週報", "global"), ("weekly", "local")])
    assert lines == ["NAME    FROM", "週報    global", "weekly  local"]
    assert {display_width(line.split("  ")[0].rstrip()) for line in lines[1:]} == {4, 6}


def test_table_escapes_control_characters_and_tabs() -> None:
    assert render_table([("NAME", "SETS"), ("a", "x\ty\nz\x1b")]) == [
        "NAME  SETS",
        "a     x\\ty\\nz\\x1b",
    ]


def test_table_cuts_only_the_chosen_column_to_the_width() -> None:
    rows = [("NAME", "SETS", "FROM"), ("n" * 20, "k=v " * 20, "global")]
    lines = render_table(rows, cut=1, width=60)
    assert display_width(lines[1]) == 60
    assert lines[1] == f"{'n' * 20}  {('k=v ' * 20)[:29]}…  global"


def test_table_never_cuts_below_twenty_cells_and_overflows_instead() -> None:
    rows = [("NAME", "SETS", "FROM"), ("n" * 20, "k=v " * 20, "global")]
    lines = render_table(rows, cut=1, width=30)
    assert lines[1] == f"{'n' * 20}  {('k=v ' * 20)[:19]}…  global"


def test_table_keeps_a_short_column_whole_on_a_narrow_terminal() -> None:
    assert render_table([("NAME", "SETS"), ("a", "short")], cut=1, width=5) == [
        "NAME  SETS",
        "a     short",
    ]


def test_table_without_a_width_cuts_nothing() -> None:
    rows = [("NAME", "SETS"), ("a", "k=v " * 30)]
    assert render_table(rows, cut=1)[1] == "a     " + "k=v " * 30


def test_templates_section_lists_sorted_templates_or_a_placeholder() -> None:
    templates = {
        "z": PromptTemplate("Last $topic", Path("a.toml")),
        "a": PromptTemplate("First $input", Path("b.toml")),
    }
    config = Config(Path("config.toml"), templates=templates)
    payload, lines = list_view(config, "templates", width=None, which=nothing_installed)
    assert payload == {
        "templates": [
            {"name": "a", "prompt": "First $input", "source": "b.toml", "parameters": []},
            {"name": "z", "prompt": "Last $topic", "source": "a.toml", "parameters": ["topic"]},
        ]
    }
    assert lines == [
        "NAME  PARAMETERS  FROM",
        "a     -           global",
        "z     topic       global",
    ]
    empty = list_view(Config(Path("config.toml")), "templates", width=None, which=nothing_installed)
    assert empty == ({"templates": []}, ["No templates configured."])


def test_full_listing_leaves_out_empty_sections() -> None:
    payload, lines = list_view(Config(Path("c.toml")), None, width=None, which=nothing_installed)
    assert payload == {
        "profiles": [],
        "workflows": [],
        "templates": [],
        "agents": payload["agents"],
    }
    assert lines == ["Agents", f"not installed: {NONE_INSTALLED}"]


def test_agents_section_drops_the_not_installed_line_when_every_agent_is_installed() -> None:
    _, lines = list_view(Config(Path("c.toml")), "agents", width=None, which=lambda name: name)
    assert lines[0] == "NAME         ALIASES"
    assert lines[1] == "claude       cc"
    assert len(lines) == len(AGENTS) + 1


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"version=1\n{text}", encoding="utf-8")
    return path


def test_from_names_local_profiles_and_the_global_ones_they_replace(tmp_path: Path) -> None:
    global_path = write(
        tmp_path / "config-home/pratfall/config.toml",
        '[profiles.shared]\nagent="codex"\n[profiles.mine]\nagent="codex"\n',
    )
    local_path = write(tmp_path / ".pratfile", '[profiles.mine]\nagent="claude"\nmodel="m"\n')
    config = load_config(cwd=tmp_path, env=os.environ)
    _, lines = list_view(config, "profiles", width=None, which=nothing_installed)
    assert lines == [
        "NAME    AGENT   EXTENDS  SETS     FROM",
        "mine    claude  -        model=m  local, replaces global",
        "shared  codex   -        -        global",
    ]
    assert profile_page(config, "mine").splitlines()[0] == (
        f"mine: profile for claude, local, replaces global ({local_path}, {global_path})"
    )


def test_sets_names_text_and_list_settings_by_key(tmp_path: Path) -> None:
    write(
        tmp_path / ".pratfile",
        '[profiles.p]\nagent="claude"\ntimeout=600.0\nfast=true\ninstructions="Be brief"\n'
        'tools=["Read"]\nprompt="Tidy ${folder} and ${depth}"\nnative_args=["--verbose"]\n',
    )
    config = load_config(cwd=tmp_path, env=os.environ)
    _, lines = list_view(config, "profiles", width=None, which=nothing_installed)
    sets = "timeout=600 fast=True instructions tools native_args prompt (folder, depth)"
    assert lines[1] == f"p     claude  -        {sets}  local"


def test_profile_page_names_the_origin_of_each_value(tmp_path: Path) -> None:
    write(
        tmp_path / "config-home/pratfall/config.toml",
        '[defaults]\neffort="low"\n[agents.claude]\ncommand_prefix=["nice"]\n'
        '[profiles.base]\nagent="claude"\nmodel="claude-opus-5"\n',
    )
    local_path = write(
        tmp_path / ".pratfile",
        '[defaults]\nnotify=false\n[profiles.weekly]\nextends="base"\neffort="high"\n'
        'prompt="Summarise ${topic}"\n',
    )
    page = profile_page(load_config(cwd=tmp_path, env=os.environ), "weekly")
    assert page == (
        f"weekly: profile for claude, local ({local_path})\n"
        "extends: base\n"
        "parameters: topic\n"
        "\n"
        "SETTING         VALUE                 ORIGIN\n"
        "model           claude-opus-5         profiles.base\n"
        "effort          high                  profiles.weekly (local)\n"
        "timeout         600                   built-in default\n"
        "notify          False                 defaults (local)\n"
        'command_prefix  ["nice"]              agents.claude\n'
        'prompt          "Summarise ${topic}"  profiles.weekly (local)'
    )


@pytest.mark.parametrize("section", ["profiles", "workflows", "templates"])
def test_an_empty_section_prints_the_configured_placeholder(section: str) -> None:
    _, lines = list_view(Config(Path("c.toml")), section, width=None, which=nothing_installed)
    assert lines == [f"No {section} configured."]
