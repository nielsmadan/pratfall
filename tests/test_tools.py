import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import copilot_result
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import BY_NAME
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, option_origins, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile

AGENTS = ("claude", "qwen", "copilot", "droid", "vibe")
PREFIXES = {
    "claude": ["-p", "--output-format", "json"],
    "qwen": ["--output-format", "stream-json"],
    "copilot": ["--output-format=json"],
    "droid": ["exec", "--output-format", "json"],
    "vibe": ["--prompt", "--output", "json"],
}
FLAGS = {
    "claude": ["--tools=Read,-literal", "--disallowedTools=Write,Edit"],
    "qwen": [
        "--core-tools=Read",
        "--core-tools=-literal",
        "--exclude-tools=Write",
        "--exclude-tools=Edit",
    ],
    "copilot": ["--available-tools=Read,-literal", "--excluded-tools=Write,Edit"],
    "droid": ["--restrict-tools=Read,-literal", "--disabled-tools=Write,Edit"],
    "vibe": [
        "--enabled-tools=Read",
        "--enabled-tools=-literal",
        "--disabled-tools=Write",
        "--disabled-tools=Edit",
    ],
}


@pytest.mark.parametrize("agent", AGENTS)
def test_tools_fake_process_receives_exact_argv(
    agent: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    log = tmp_path / "argv.json"
    native_output = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    if agent == "copilot":
        native_output = (
            json.dumps({"type": "assistant.message", "data": {"messageId": "1", "content": "ok"}})
            + "\n"
            + json.dumps(copilot_result())
        )
    elif agent == "vibe":
        native_output = (
            '[{"type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]}]'
        )
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps([sys.argv[1:], sys.stdin.read()]))\n"
        f"print({native_output!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert (
        main(
            [
                "--tools",
                "Read",
                agent,
                "--tools=-literal",
                "--disable-tools",
                "Write",
                "task",
                "--disable-tools=Edit",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    argv, stdin = json.loads(log.read_text())
    assert argv == [
        *PREFIXES[agent],
        *FLAGS[agent],
        *(["--prompt=task"] if agent == "copilot" else []),
    ]
    assert stdin == ("" if agent == "copilot" else "task")


@pytest.mark.parametrize(
    "agent,allow,deny,expected",
    [
        (
            "claude",
            "Read",
            "Bash(git push *)",
            ["--tools=Read", "--disallowedTools=Bash(git push *)"],
        ),
        (
            "qwen",
            "ShellTool",
            "Shell(rm *)",
            ["--core-tools=ShellTool", "--exclude-tools=Shell(rm *)"],
        ),
        (
            "vibe",
            r"re:^(read|search)[, ]",
            "bash*",
            [r"--enabled-tools=re:^(read|search)[, ]", "--disabled-tools=bash*"],
        ),
    ],
)
def test_native_patterns_are_preserved(
    agent: str, allow: str, deny: str, expected: list[str]
) -> None:
    resolved = ResolvedProfile(
        BY_NAME[agent], None, ("fake",), Options(tools=(allow,), disabled_tools=(deny,))
    )
    ADAPTERS[agent].validate(resolved)
    assert ADAPTERS[agent].build(resolved, b"task").argv == ("fake", *PREFIXES[agent], *expected)


def test_tool_arrays_replace_all_four_layers_and_keep_winning_origins(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        'version=1\n[defaults]\ntools=["Global"]\ndisabled_tools=["GlobalDeny"]\n'
    )
    local = tmp_path / ".pratfile"
    local.write_text(
        'version=1\n[defaults]\ntools=["Local"]\ndisabled_tools=["LocalDeny"]\n'
        '[profiles.work]\nagent="claude"\ntools=["Profile"]\ndisabled_tools=["ProfileDeny"]\n'
        '[profiles.clear]\nagent="copilot"\ntools=[]\ndisabled_tools=[]\n'
    )
    config = load_config()
    assert resolve_profile(config, "claude").options.tools == ("Local",)
    assert resolve_profile(config, "claude").options.disabled_tools == ("LocalDeny",)
    assert resolve_profile(config, "work").options.tools == ("Profile",)
    assert resolve_profile(config, "work").options.disabled_tools == ("ProfileDeny",)
    cli = Options(tools=("CLI", "CLI"), disabled_tools=())
    assert resolve_profile(config, "work", cli).options.tools == ("CLI", "CLI")
    assert resolve_profile(config, "work", cli).options.disabled_tools == ()
    assert resolve_profile(config, "clear").options.tools == ()
    assert resolve_profile(config, "clear").options.disabled_tools == ()
    assert option_origins(config, "work")["tools"].label == f"{local}: profiles.work.tools"
    assert option_origins(config, "work", cli)["disabled_tools"].code == "invalid_arguments"
    local.unlink()
    assert resolve_profile(load_config(), "claude").options.tools == ("Global",)


@pytest.mark.parametrize("field", ["tools", "disabled_tools"])
@pytest.mark.parametrize("value", ['"Read"', "true", "[1]", '[""]', '[" "]', '["\\u0000"]'])
def test_tool_config_requires_array_of_nonempty_names(
    field: str, value: str, tmp_path: Path
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(f"version=1\n[defaults]\n{field}={value}\n")
    with pytest.raises(PratError, match=rf"defaults.{field}"):
        load_config()


@pytest.mark.parametrize(
    "agent,expected",
    [
        ("claude", ["--tools="]),
        ("copilot", ["--available-tools", "--prompt=task"]),
    ],
)
def test_empty_allowlist_has_explicit_native_transport(
    agent: str, expected: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text(f'version=1\n[profiles.empty]\nagent="{agent}"\ntools=[]\n')
    assert main(["empty", "task", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"] == [agent, *PREFIXES[agent], *expected]
    assert main(["profiles"]) == 0
    assert "tools=[]" in capsys.readouterr().out


@pytest.mark.parametrize("agent", ["qwen", "droid", "vibe", "codex"])
def test_empty_allowlist_rejected_with_config_source(
    agent: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(f'version=1\n[profiles.empty]\nagent="{agent}"\ntools=[]\n')
    assert main(["config", "validate", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{path}: profiles.empty.tools" in error["message"]


@pytest.mark.parametrize("agent", sorted(BY_NAME))
def test_empty_disabled_tools_neutral_for_every_agent(agent: str, tmp_path: Path) -> None:
    (tmp_path / ".pratfile").write_text(
        f'version=1\n[defaults]\ndisabled_tools=["Write"]\n[profiles.clear]\nagent="{agent}"\ndisabled_tools=[]\n'
    )
    resolved = resolve_profile(load_config(), "clear")
    assert resolved.options.disabled_tools == ()
    ADAPTERS[agent].validate(resolved)


@pytest.mark.parametrize(
    "agent,public,native",
    [
        ("claude", "--tools", "--tools=Read"),
        ("claude", "--disable-tools", "--disallowedTools=Write"),
        ("claude", "--disable-tools", "--disallowed-tools=Write"),
        ("qwen", "--tools", "--core-tools=Read"),
        ("qwen", "--disable-tools", "--exclude-tools=Write"),
        ("copilot", "--tools", "--available-tools"),
        ("copilot", "--tools", "--enable-mcp-server=server"),
        ("copilot", "--tools", "--enable-all-github-mcp-tools"),
        ("copilot", "--tools", "--add-github-mcp-tool=tool"),
        ("copilot", "--tools", "--add-github-mcp-toolset=tools"),
        ("copilot", "--disable-tools", "--excluded-tools=Write"),
        ("droid", "--tools", "--restrict-tools=Read"),
        ("droid", "--tools", "--additional-tools=Write"),
        ("droid", "--disable-tools", "--disabled-tools=Write"),
        ("droid", "--disable-tools", "--additional-tools=Write"),
        ("vibe", "--tools", "--enabled-tools=Read"),
        ("vibe", "--disable-tools", "--disabled-tools=Write"),
    ],
)
def test_tool_conflicts_fail_before_input(
    agent: str,
    public: str,
    native: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt must not be acquired")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main([agent, public, "Read", "--json", "--", native]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "controlled by prat" in error["message"]


@pytest.mark.parametrize(
    "agent,native",
    [
        ("claude", ("--tools=Read", "--disallowed-tools=Bash(rm *)")),
        ("copilot", ("--available-tools", "Read", "--excluded-tools=Write")),
        ("vibe", ("--enabled-tools=read*", "--disabled-tools=bash")),
    ],
)
def test_native_only_tool_arguments_stay_accepted(agent: str, native: tuple[str, ...]) -> None:
    resolved = ResolvedProfile(
        BY_NAME[agent], None, ("fake",), Options(native_args=native, disabled_tools=())
    )
    ADAPTERS[agent].validate(resolved)
    suffix = ("--prompt=task",) if agent == "copilot" else ()
    assert ADAPTERS[agent].build(resolved, b"task").argv == (
        "fake",
        *PREFIXES[agent],
        *native,
        *suffix,
    )


@pytest.mark.parametrize(
    "agent,value",
    [
        ("claude", "Read,Write"),
        ("claude", " Read"),
        ("claude", "Read Write"),
        ("qwen", "Read,Write"),
        ("qwen", "Read\ufeff"),
        ("copilot", "Read,Write"),
        ("copilot", " Read"),
        ("droid", "Read,Write"),
        ("droid", "Read Write"),
        ("droid", "Read\tWrite"),
        ("droid", "Read\ufeffWrite"),
        ("claude", "Read\ufeffWrite"),
        ("vibe", "\ud800"),
    ],
)
def test_unrepresentable_tool_values_fail_before_input(
    agent: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt must not be acquired")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main([agent, "--tools", value, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "tools" in error["message"]


@pytest.mark.parametrize("flag", ["--tools", "--disable-tools"])
def test_unsupported_tools_rejected_before_input(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt must not be acquired")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main(["codex", flag, "Read", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_arguments"


def test_tool_capability_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {r["name"] for r in records if r["capabilities"]["tools"]} == set(AGENTS)
    assert {r["name"] for r in records if r["capabilities"]["disabled_tools"]} == set(AGENTS)
    assert {r["name"] for r in records if r["capabilities"]["tools_empty"]} == {"claude", "copilot"}
    claude = next(r["capabilities"] for r in records if r["name"] == "claude")
    assert claude["tools_scope"] == "built-ins; MCP unaffected; EndConversation may remain"
    assert claude["disabled_tools_scope"] == "native deny rules; EndConversation exception"


@pytest.mark.parametrize(
    "agent,native", [("claude", "--tools=Read"), ("copilot", "--available-tools=read")]
)
def test_active_empty_allowlist_conflicts_with_native_flags(
    agent: str,
    native: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".pratfile").write_text(f'version=1\n[profiles.empty]\nagent="{agent}"\ntools=[]\n')

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt must not be acquired")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main(["empty", "--json", "--", native]) == 2
    assert "controlled by prat" in json.loads(capsys.readouterr().out)["error"]["message"]


@pytest.mark.parametrize(
    "agent,native",
    [
        ("claude", ("--disallowed-tools=Write",)),
        ("copilot", ("--deny-tool=write",)),
        ("vibe", ("--disabled-tools=bash",)),
        ("qwen", ("--approval-mode=default",)),
    ],
)
def test_tool_allowlist_retains_native_denials(agent: str, native: tuple[str, ...]) -> None:
    resolved = ResolvedProfile(
        BY_NAME[agent], None, ("fake",), Options(tools=("Read",), native_args=native)
    )
    ADAPTERS[agent].validate(resolved)
    tool_flag = {
        "claude": "--tools=Read",
        "copilot": "--available-tools=Read",
        "vibe": "--enabled-tools=Read",
        "qwen": "--core-tools=Read",
    }[agent]
    suffix = ("--prompt=task",) if agent == "copilot" else ()
    assert ADAPTERS[agent].build(resolved, b"task").argv == (
        "fake",
        *PREFIXES[agent],
        tool_flag,
        *native,
        *suffix,
    )


@pytest.mark.parametrize("field", ["tools", "disabled_tools"])
def test_invalid_config_tool_delimiters_name_winning_source(
    field: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(
        f'version=1\n[defaults]\n{field}=["Read,Write"]\n[profiles.work]\nagent="claude"\n'
    )
    assert main(["config", "validate", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{path}: defaults.{field}" in error["message"]


@pytest.mark.parametrize("field", ["tools", "disabled_tools"])
def test_unsupported_inherited_tool_defaults_reject_even_unused_profiles(
    field: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".pratfile"
    path.write_text(f'version=1\n[defaults]\n{field}=["Read"]\n[profiles.unused]\nagent="codex"\n')
    assert main(["claude", "task", "--dry-run", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{path}: defaults.{field}" in error["message"]
