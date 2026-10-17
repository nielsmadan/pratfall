import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pratfall.catalog import AGENTS, BY_SELECTOR, MANAGEMENT_COMMANDS
from pratfall.cli import main
from pratfall.cli.parsing import _CONFIG_COMMAND_HELP, _EPILOG, _MANAGEMENT_HELP, build_parser
from pratfall.cli.shell_completion import (
    _DIR_FLAGS,
    _FILE_FLAGS,
    _TEMPLATE_FLAGS,
    Loader,
    _complete,
    _rejoin_wordbreaks,
    _run_flags,
)
from pratfall.config import load_config
from pratfall.local_trust import trust
from pratfall.models import Config, Profile

PRATFILE = """version=1
[profiles.simple]
agent="claude"
[profiles.quick]
agent="gemini"
[profiles.cleaner]
agent="claude"
prompt="Remove empty files under $folder keeping $keep"
[templates.review]
prompt="Review $input"
[templates.move]
prompt="Move $source to $target"
"""


def loader(cwd: Path) -> Loader:
    return lambda explicit: load_config(explicit, cwd=cwd, env=os.environ)


def words(arguments: list[str], cwd: Path) -> list[str]:
    result = _complete(arguments, loader(cwd))
    assert result.mode == "words"
    return [word for word, _ in result.candidates]


def callback(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(["__complete", *arguments]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out.splitlines()


@pytest.fixture
def shim(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prat = bin_dir / "prat"
    prat.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pratfall "$@"\n', encoding="utf-8")
    prat.chmod(0o755)
    return bin_dir


def script(shell: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["completion", shell]) == 0
    return capsys.readouterr().out


def test_completion_zsh_prints_dual_mode_script(capsys: pytest.CaptureFixture[str]) -> None:
    output = script("zsh", capsys)
    assert output.startswith("#compdef prat\n")
    assert "command prat __complete" in output
    assert "compdef _prat prat" in output
    assert "_files -/" in output


def test_completion_bash_prints_registration(capsys: pytest.CaptureFixture[str]) -> None:
    output = script("bash", capsys)
    assert "command prat __complete" in output
    assert output.endswith("complete -o default -F _prat prat\n")


def test_completion_autodetects_shell_from_shell_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert main(["completion"]) == 0
    assert capsys.readouterr().out.startswith("#compdef prat")


@pytest.mark.parametrize("value", [None, ""])
def test_completion_falls_back_to_bash(
    value: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if value is None:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", value)
    assert main(["completion"]) == 0
    assert capsys.readouterr().out.endswith("complete -o default -F _prat prat\n")


@pytest.mark.parametrize(
    ("arguments", "shell"), [(["completion", "fish"], None), (["completion"], "/usr/bin/fish")]
)
def test_completion_rejects_unsupported_shell(
    arguments: list[str],
    shell: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if shell is not None:
        monkeypatch.setenv("SHELL", shell)
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "prat: Unsupported shell 'fish'; run 'prat completion bash' or 'prat completion zsh'.\n"
    )


def test_completion_rejects_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "completion", "zsh"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error == {
        "code": "invalid_arguments",
        "message": "completion prints shell code; --json is not supported.",
    }
    assert main(["completion", "zsh", "--json"]) == 2


def test_completion_ignores_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text("not toml [", encoding="utf-8")
    assert script("zsh", capsys).startswith("#compdef prat")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_bash_script_parses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "prat.bash"
    path.write_text(script("bash", capsys), encoding="utf-8")
    subprocess.run(["bash", "-n", str(path)], check=True)


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")
def test_zsh_script_parses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "_prat"
    path.write_text(script("zsh", capsys), encoding="utf-8")
    subprocess.run(["zsh", "-n", str(path)], check=True)


BASH_DRIVER = """source "$1"
read -ra COMP_WORDS <<<"$2"
[[ $2 == *" " ]] && COMP_WORDS+=("")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
_prat
printf '%s\\n' "${COMPREPLY[@]}"
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("prat co", ["co", "codex", "completion", "config", "copilot", "cortex"]),
        ("prat cc --effort ", ["high", "low", "max", "medium", "xhigh"]),
        ("prat cc --cwd s", ["sub-one", "sub-two"]),
    ],
)
def test_bash_glue_end_to_end(
    line: str,
    expected: list[str],
    shim: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "sub-one").mkdir()
    (tmp_path / "sub-two").mkdir()
    (tmp_path / "stray-file").touch()
    path = tmp_path / "prat.bash"
    path.write_text(script("bash", capsys), encoding="utf-8")
    driver = tmp_path / "driver.bash"
    driver.write_text(BASH_DRIVER, encoding="utf-8")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", str(driver), str(path), line],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": f"{shim}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(tmp_path / "config-home")},
    )
    assert sorted(result.stdout.split()) == expected


ZSH_DRIVER = """source "$1"
_files() { print -r -- "_files${1:+ $*}"; }
_describe() { print -r -- "_describe ${(j:|:)${(P)4}}${5:+ ${(j: :)${(q)@[5,-1]}}}"; }
words=(${(z)2})
[[ $2 == *" " ]] && words+=("")
CURRENT=$#words
_prat
"""


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("prat cl", "_describe claude:Claude Code|claw:OpenClaw"),
        ("prat cc --pro", "_describe --progress:Print bounded live activity updates on stderr."),
        ("prat cc --add-dir ", "_files -/"),
        ("prat cc -f ", "_files"),
    ],
)
def test_zsh_glue_with_stubbed_builtins(
    line: str,
    expected: str,
    shim: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "_prat"
    path.write_text(script("zsh", capsys), encoding="utf-8")
    driver = tmp_path / "driver.zsh"
    driver.write_text(ZSH_DRIVER, encoding="utf-8")
    result = subprocess.run(
        ["zsh", "-f", str(driver), str(path), line],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": f"{shim}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(tmp_path / "config-home")},
    )
    assert result.stdout.splitlines()[0] == expected


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")
def test_zsh_glue_keeps_equals_flags_open(
    shim: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "_prat"
    path.write_text(script("zsh", capsys), encoding="utf-8")
    driver = tmp_path / "driver.zsh"
    driver.write_text(ZSH_DRIVER, encoding="utf-8")
    result = subprocess.run(
        ["zsh", "-f", str(driver), str(path), "prat cc --prom"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": f"{shim}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(tmp_path / "config-home")},
    )
    assert result.stdout.splitlines() == [
        "_describe --prompt=:Pass prompt text, including text beginning with a dash. -S ''"
    ]


def test_selector_slot_offers_agents_aliases_and_management(tmp_path: Path) -> None:
    expected = {*BY_SELECTOR, *MANAGEMENT_COMMANDS}
    assert set(words([""], tmp_path)) == expected


def test_selector_descriptions(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    candidates = dict(_complete(["c"], loader(tmp_path)).candidates)
    assert candidates["cc"] == "Claude Code"
    assert candidates["config"] == "Manage TOML configuration."
    assert dict(_complete(["s"], loader(tmp_path)).candidates) == {"simple": "profile: claude"}


def test_prefix_filters_and_sorts(tmp_path: Path) -> None:
    assert words(["co"], tmp_path) == ["co", "codex", "completion", "config", "copilot", "cortex"]


def test_local_pratfile_profiles_are_offered(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["qu"], tmp_path) == ["quick"]


@pytest.mark.usefixtures("real_trust")
def test_untrusted_pratfile_names_are_hidden_until_trusted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "config-home" / "pratfall"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'version=1\n[profiles.quiet]\nagent="codex"\n', encoding="utf-8"
    )
    pratfile = tmp_path / ".pratfile"
    pratfile.write_text(PRATFILE, encoding="utf-8")
    assert callback(["qu"], capsys) == ["words", "quiet\tprofile: codex"]
    trust(pratfile, pratfile.read_bytes(), os.environ)
    assert callback(["qu"], capsys) == ["words", "quick\tprofile: gemini", "quiet\tprofile: codex"]


def test_explicit_config_flag_is_honored(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    (tmp_path / "other.toml").write_text(
        'version=1\n[profiles.elsewhere]\nagent="codex"\n', encoding="utf-8"
    )
    assert words(["--config", "other.toml", "e"], tmp_path) == ["elsewhere"]
    assert words(["--config", "'other.toml'", "s"], tmp_path) == []


def test_run_option_hides_management_commands(tmp_path: Path) -> None:
    assert "config" not in words(["--model", "x", ""], tmp_path)
    assert "config" in words(["--json", ""], tmp_path)


def test_value_of_value_flag_is_not_a_selector(tmp_path: Path) -> None:
    assert words(["--model", "cc", "cod"], tmp_path) == ["codex"]


def test_top_level_flags_include_help_and_version(tmp_path: Path) -> None:
    assert words(["--v"], tmp_path) == ["--version"]
    assert words(["-h"], tmp_path) == ["-h"]


def test_run_flags_after_selector(tmp_path: Path) -> None:
    assert words(["cc", "--no-"], tmp_path) == ["--no-fast", "--no-notify"]
    assert {"-f", "-t", "--prompt="} <= set(words(["cc", "-"], tmp_path))
    assert "--version" not in words(["cc", "-"], tmp_path)


def test_prompt_slot_offers_nothing(tmp_path: Path) -> None:
    assert words(["cc", "hel"], tmp_path) == []


def test_template_values_from_config(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cc", "-t", ""], tmp_path) == ["move", "review"]
    assert words(["--template", "r"], tmp_path) == ["review"]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("cc", ["high", "low", "max", "medium", "xhigh"]),
        ("simple", ["high", "low", "max", "medium", "xhigh"]),
        ("gemini", []),
    ],
)
def test_effort_values_follow_selected_agent(
    selector: str, expected: list[str], tmp_path: Path
) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words([selector, "--effort", ""], tmp_path) == expected


def test_effort_without_selector_offers_every_known_value(tmp_path: Path) -> None:
    expected = {value for agent in AGENTS for value in agent.capabilities.effort_values}
    assert set(words(["--effort", ""], tmp_path)) == expected


@pytest.mark.parametrize("flag", sorted(_DIR_FLAGS | _FILE_FLAGS))
def test_path_flags_delegate_to_shell(flag: str, tmp_path: Path) -> None:
    expected = "dirs" if flag in _DIR_FLAGS else "files"
    assert _complete(["cc", flag, ""], loader(tmp_path)).mode == expected


@pytest.mark.parametrize("flag", ["--model", "--timeout"])
def test_free_value_flags_offer_nothing(flag: str, tmp_path: Path) -> None:
    assert words(["cc", flag, ""], tmp_path) == []


def test_equals_form_is_not_completed(tmp_path: Path) -> None:
    assert words(["cc", "--model=gp"], tmp_path) == []


def test_native_arguments_delegate_to_shell(tmp_path: Path) -> None:
    assert _complete(["cc", "hi", "--", "--x"], loader(tmp_path)).mode == "files"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["config", ""], ["init", "path", "trust", "validate"]),
        (["config", "path", ""], []),
        (["completion", ""], ["bash", "zsh"]),
        (["completion", "--"], ["--help"]),
        (["doctor", "--"], ["--config", "--help", "--json", "--versions"]),
        (["agents", "--"], ["--config", "--help", "--json"]),
        (["agents", ""], []),
    ],
)
def test_management_choices_and_flags(
    arguments: list[str], expected: list[str], tmp_path: Path
) -> None:
    assert words(arguments, tmp_path) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (["--prompt", "=", "hi", "cc"], ["--prompt=hi", "cc"]),
        (["--template", "="], ["--template="]),
        (["cc", "a", "=", "b"], ["cc", "a", "=", "b"]),
        (["--param", "folder", "=", "src"], ["--param", "folder=src"]),
        (["--param", "folder", "="], ["--param", "folder="]),
        (["--param", "="], ["--param="]),
        (["--param", "=", "a", "=", "b"], ["--param=a=b"]),
    ],
)
def test_bash_equals_wordbreak_is_rejoined(given: list[str], expected: list[str]) -> None:
    assert _rejoin_wordbreaks(given) == expected


def test_rejoined_prompt_is_not_a_selector(tmp_path: Path) -> None:
    assert words(["--prompt", "=", "hi", "cc", "--no-f"], tmp_path) == ["--no-fast"]


def test_invalid_config_offers_static_candidates_silently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text('version=1\n[profiles.simple]\nagent="nope"\n')
    lines = callback([""], capsys)
    assert lines[0] == "words"
    names = {line.split("\t")[0] for line in lines[1:]}
    assert "codex" in names
    assert "simple" not in names


def test_callback_never_spawns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_: object, **__: object) -> None:
        pytest.fail("completion launched a process")

    monkeypatch.setattr(subprocess, "Popen", fail)
    assert callback(["c"], capsys)[0] == "words"


def test_callback_output_format(capsys: pytest.CaptureFixture[str]) -> None:
    assert callback(["conf"], capsys) == ["words", "config\tManage TOML configuration."]
    assert callback(["cc", "--model", ""], capsys) == ["words"]


def test_callback_without_words_is_selector_slot(capsys: pytest.CaptureFixture[str]) -> None:
    assert "codex\tCodex" in callback([], capsys)


def test_callback_survives_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pratfall.cli.shell_completion._complete", broken)
    assert callback(["c"], capsys) == ["words"]


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return dict(action.choices)


def test_management_tables_match_parser() -> None:
    commands = subcommands(build_parser())
    assert set(commands) == set(_MANAGEMENT_HELP) == MANAGEMENT_COMMANDS
    assert set(subcommands(commands["config"])) == set(_CONFIG_COMMAND_HELP)


def test_path_and_template_flags_are_run_value_flags() -> None:
    run_flags = _run_flags()
    assert (_DIR_FLAGS | _FILE_FLAGS | _TEMPLATE_FLAGS | {"--effort"}) <= set(run_flags)


def test_every_help_run_flag_is_completed_with_a_description() -> None:
    section = _EPILOG.split("run options:\n", 1)[1].split("\n\n", 1)[0]
    documented = set(re.findall(r"(?<![\w-])--?[a-z][\w-]*=?", section))
    run_flags = _run_flags()
    assert documented == set(run_flags)
    assert all(run_flags.values())


def candidates(arguments: list[str], cwd: Path) -> list[tuple[str, str]]:
    result = _complete(arguments, loader(cwd))
    assert result.mode == "words"
    return list(result.candidates)


def test_param_offers_profile_parameters(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert candidates(["cleaner", "--param", ""], tmp_path) == [
        ("folder=", "parameter"),
        ("keep=", "parameter"),
    ]


def test_param_skips_bound_names(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cleaner", "--param", "folder=src", "--param", ""], tmp_path) == ["keep="]
    assert words(["cleaner", "--param=folder=src", "--param", ""], tmp_path) == ["keep="]


def test_param_skips_names_bound_by_position(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cleaner", "x", "--param", ""], tmp_path) == ["keep="]
    assert words(["cleaner", "x", "y", "--param", ""], tmp_path) == []


def test_positional_after_all_parameters_bound_offers_nothing(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert _complete(["cleaner", "x", "sr"], loader(tmp_path)).mode == "files"
    assert _complete(["cleaner", "x", "--param", "keep=y", "sr"], loader(tmp_path)).mode == "words"
    assert _complete(["cleaner", "x", "y", "sr"], loader(tmp_path)).mode == "words"
    named = ["cleaner", "--param", "folder=x", "--param=keep=y", "sr"]
    assert _complete(named, loader(tmp_path)).mode == "words"


def test_param_offers_template_parameters_over_profile(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cleaner", "-t", "move", "--param", ""], tmp_path) == ["source=", "target="]
    assert words(["-t=move", "cleaner", "--param", "t"], tmp_path) == ["target="]


def test_param_with_unknown_template_offers_nothing(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cleaner", "-t", "nope", "--param", ""], tmp_path) == []


def test_param_without_active_template_offers_nothing(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["simple", "--param", ""], tmp_path) == []


def test_param_value_part_is_not_completed(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert words(["cleaner", "--param", "folder=s"], tmp_path) == []


def test_positional_value_in_parameter_mode_completes_paths(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    assert _complete(["cleaner", "sr"], loader(tmp_path)).mode == "files"
    assert _complete(["cc", "-t", "move", "sr"], loader(tmp_path)).mode == "files"
    assert _complete(["cc", "-t", "review", "sr"], loader(tmp_path)).mode == "words"


def test_bash_split_param_words_resolve_the_selector(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(PRATFILE, encoding="utf-8")
    for split in (["--param", "folder", "=", "src"], ["--param", "=", "folder", "=", "src"]):
        assert words([*split, "cleaner", "--effort", ""], tmp_path) == [
            "high",
            "low",
            "max",
            "medium",
            "xhigh",
        ]


def test_param_is_a_described_flag(tmp_path: Path) -> None:
    assert dict(candidates(["cc", "--par"], tmp_path)) == {
        "--param": "Fill a prompt parameter; repeat for several."
    }


BASH_SPLIT_DRIVER = BASH_DRIVER.replace('<<<"$2"', '<<<"${2//=/ = }"')


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("prat cleaner --param ", ["folder=", "keep="]),
        ("prat cleaner --param folder=src --param ", ["keep="]),
        ("prat cleaner --param=folder=src --effort ", ["high", "low", "max", "medium", "xhigh"]),
    ],
)
def test_bash_glue_completes_params(
    line: str,
    expected: list[str],
    shim: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pratfile = tmp_path / ".pratfile"
    pratfile.write_text(PRATFILE, encoding="utf-8")
    trust(pratfile, pratfile.read_bytes(), os.environ)
    path = tmp_path / "prat.bash"
    path.write_text(script("bash", capsys), encoding="utf-8")
    driver = tmp_path / "driver.bash"
    driver.write_text(BASH_SPLIT_DRIVER, encoding="utf-8")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", str(driver), str(path), line],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env={
            "PATH": f"{shim}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
            "XDG_STATE_HOME": str(tmp_path / "state-home"),
        },
    )
    assert sorted(result.stdout.split()) == expected


WORKFLOW_PRATFILE = (
    PRATFILE
    + """[workflows.tidy]
steps = [
  { name = "first", profile = "simple" },
  { name = "second", profile = "cleaner", after = ["first"], bind = { keep = "first" } },
  { name = "third", profile = "simple" },
]
"""
)


def test_workflow_names_complete_as_selectors(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(WORKFLOW_PRATFILE, encoding="utf-8")
    assert candidates(["ti"], tmp_path) == [("tidy", "workflow")]


def test_param_offers_workflow_parameters(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(WORKFLOW_PRATFILE, encoding="utf-8")
    assert candidates(["tidy", "--param", ""], tmp_path) == [("folder=", "parameter")]
    assert words(["tidy", "--param", "folder=src", "--param", ""], tmp_path) == []


def test_param_skips_each_as_parameter(tmp_path: Path) -> None:
    pratfile = (
        PRATFILE
        + """[workflows.fan]
steps = [
  { name = "first", profile = "simple" },
  { name = "each", profile = "cleaner", each = "first", as = "keep" },
]
"""
    )
    (tmp_path / ".pratfile").write_text(pratfile, encoding="utf-8")
    assert candidates(["fan", "--param", ""], tmp_path) == [("folder=", "parameter")]


def test_workflow_positional_slots_complete_files_while_unbound(tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(WORKFLOW_PRATFILE, encoding="utf-8")
    assert _complete(["tidy", "sr"], loader(tmp_path)).mode == "files"
    assert _complete(["tidy", "x", "sr"], loader(tmp_path)).mode == "words"


def test_completion_reads_profiles_and_config_path_through_the_loader() -> None:
    requested: list[str | None] = []

    def load(explicit: str | None) -> Config:
        requested.append(explicit)
        return Config(Path("x.toml"), profiles={"night": Profile("claude")})

    result = _complete(["--config", "~/team.toml", "ni"], load)
    assert result.candidates == (("night", "profile: claude"),)
    assert requested == [str(Path("~/team.toml").expanduser())]
