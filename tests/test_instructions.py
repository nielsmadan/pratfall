import json
import os
import signal
import sys
import tomllib
from pathlib import Path

import pytest

from pratfall import instructions
from pratfall.adapters.registry import ADAPTERS
from pratfall.catalog import BY_NAME
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, option_origins, resolve_profile
from pratfall.errors import PratError
from pratfall.instructions import INSTRUCTIONS_LIMIT, read_instructions, validate_instructions
from pratfall.models import OptionOrigin, Options, ResolvedProfile
from pratfall.prompt_input import InputInterrupted

TEXT = '-rules "quoted"\n\\path $(touch unwanted) `command` $HOME 🚀\t\b\f\r\x01\x7f'


@pytest.mark.parametrize("agent", ["claude", "codex", "qwen", "droid"])
def test_instruction_native_argv_round_trips_literal_text(agent: str) -> None:
    resolved = ResolvedProfile(BY_NAME[agent], None, ("fake",), Options(instructions=TEXT))
    invocation = ADAPTERS[agent].build(resolved, b"task")
    if agent == "codex":
        assert invocation.argv[:4] == ("fake", "exec", "--json", "-c")
        assert tomllib.loads(invocation.argv[4]) == {"developer_instructions": TEXT}
        assert invocation.argv[5:] == ("-",)
    else:
        prefix = {
            "claude": ("fake", "-p", "--output-format", "json"),
            "qwen": ("fake", "--output-format", "stream-json"),
            "droid": ("fake", "exec", "--output-format", "json"),
        }[agent]
        assert invocation.argv == (*prefix, f"--append-system-prompt={TEXT}")
    assert invocation.stdin == b"task"


@pytest.mark.parametrize("agent", ["claude", "codex", "qwen", "droid"])
@pytest.mark.parametrize("file_form", [False, True])
def test_instruction_fake_process_receives_literal_argv_and_prompt(
    agent: str, file_form: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    log = tmp_path / "call.json"
    output = {
        "claude": (
            '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
            '"usage":{"input_tokens":1,"output_tokens":1}}'
        ),
        "droid": '{"type":"result","subtype":"success","is_error":false,"result":"ok"}',
        "qwen": '{"type":"result","subtype":"success","is_error":false,"result":"ok"}',
        "codex": (
            '{"type":"item.completed","item":{"id":"1","type":"agent_message","text":"ok"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}'
        ),
    }[agent]
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps([sys.argv[1:], sys.stdin.read()]))\n"
        f"print({output!r})\n"
    )
    config = tmp_path / ".pratfile"
    config.write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    path = tmp_path / "rules.txt"
    path.write_text(TEXT, encoding="utf-8", newline="")
    flag, value = ("--instructions-file", str(path)) if file_form else ("--instructions", TEXT)
    assert main([agent, flag, value, "task", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    argv, prompt = json.loads(log.read_text())
    if agent == "codex":
        assert argv[:3] == ["exec", "--json", "-c"]
        assert tomllib.loads(argv[3]) == {"developer_instructions": TEXT}
        assert argv[4:] == ["-"]
    else:
        assert (
            argv
            == {
                "claude": ["-p", "--output-format", "json", f"--append-system-prompt={TEXT}"],
                "droid": ["exec", "--output-format", "json", f"--append-system-prompt={TEXT}"],
                "qwen": ["--output-format", "stream-json", f"--append-system-prompt={TEXT}"],
            }[agent]
        )
    assert prompt == "task"
    assert not (tmp_path / "unwanted").exists()


@pytest.mark.parametrize("first_file", [False, True])
def test_four_layer_instruction_group_replaces_value_and_origin(
    first_file: bool, tmp_path: Path
) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    local = tmp_path / "local" / "config.toml"
    local.parent.mkdir()
    global_field, local_field = (
        ("instructions_file", "instructions")
        if first_file
        else ("instructions", "instructions_file")
    )
    global_path.write_text(f'version=1\n[defaults]\n{global_field}="global"\n')
    local.write_text(f'version=1\n[defaults]\n{local_field}="local"\n')
    config = load_config(local)
    resolved = resolve_profile(config, "cx")
    assert getattr(resolved.options, global_field) is None
    expected = str(local.parent / "local") if local_field == "instructions_file" else "local"
    assert getattr(resolved.options, local_field) == expected
    assert set(config.default_sources) == {local_field}
    assert config.default_sources[local_field] == local
    local.write_text(
        local.read_text() + f'[profiles.work]\nagent="codex"\n{global_field}="profile"\n'
    )
    config = load_config(local)
    resolved = resolve_profile(config, "work")
    assert getattr(resolved.options, local_field) is None
    assert set(option_origins(config, "work")) == {global_field}
    assert f"profiles.work.{global_field}" in option_origins(config, "work")[global_field].label
    overrides = (
        Options(instructions="cli")
        if local_field == "instructions"
        else Options(instructions_file="cli")
    )
    resolved = resolve_profile(config, "work", overrides)
    assert getattr(resolved.options, global_field) is None
    assert getattr(resolved.options, local_field) == "cli"
    origins = option_origins(config, "work", overrides)
    assert set(origins) == {local_field}
    assert origins[local_field].code == "invalid_arguments"


@pytest.mark.parametrize("scope", ["defaults", "profiles.work"])
def test_both_instruction_forms_in_one_config_layer_are_rejected(
    scope: str, tmp_path: Path
) -> None:
    path = tmp_path / ".pratfile"
    agent = 'agent="codex"\n' if scope.startswith("profiles") else ""
    path.write_text(
        f'version=1\n[{scope}]\n{agent}instructions="rules"\ninstructions_file="rules"\n'
    )
    with pytest.raises(PratError, match=f"{scope}.*cannot be used together"):
        load_config()


@pytest.mark.parametrize(
    "value", [None, 1, "", " \n", "a\0b", "\ud800", "é" * (INSTRUCTIONS_LIMIT // 2 + 1)]
)
def test_invalid_instruction_text(value: object) -> None:
    with pytest.raises(PratError) as caught:
        validate_instructions(value, OptionOrigin("source.field"))
    assert caught.value.code == "invalid_config"
    assert "source.field" in str(caught.value)


def test_instruction_limit_is_utf8_bytes_and_preserves_text(tmp_path: Path) -> None:
    text = "é" * (INSTRUCTIONS_LIMIT // 2)
    assert validate_instructions(text, OptionOrigin("text")) == text
    path = tmp_path / "instructions"
    path.write_bytes(text.encode("utf-8"))
    assert read_instructions(str(path), OptionOrigin("file")) == text


@pytest.mark.parametrize(
    "content", [b"", b" \r\n", b"\x00", b"\xff", b"a" * (INSTRUCTIONS_LIMIT + 1)]
)
def test_invalid_instruction_file_content(content: bytes, tmp_path: Path) -> None:
    path = tmp_path / "instructions"
    path.write_bytes(content)
    with pytest.raises(PratError) as caught:
        read_instructions(str(path), OptionOrigin("file.source", "invalid_arguments"))
    assert caught.value.code == "invalid_arguments"
    assert "file.source" in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_instruction_files_must_be_regular_without_blocking(kind: str, tmp_path: Path) -> None:
    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(PratError, match=r"file\.source"):
        read_instructions(str(path), OptionOrigin("file.source"))


def test_instruction_interruption_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*args: object, **kwargs: object) -> bytes:
        raise InputInterrupted(signal.SIGINT)

    monkeypatch.setattr(instructions, "read_bounded_file", interrupted)
    with pytest.raises(InputInterrupted) as caught:
        read_instructions("path", OptionOrigin("config"))
    assert caught.value.signum == signal.SIGINT


@pytest.mark.parametrize(
    "flags, message",
    [
        (["gemini", "--instructions-file", "missing"], "does not support appended instructions"),
        (["claude", "--instructions-file", "missing"], "Cannot open instructions file"),
        (
            ["claude", "--instructions", "rules", "--instructions-file", "missing"],
            "cannot be used together",
        ),
        (
            ["claude", "--instructions", "rules", "--", "--append-system-prompt=other"],
            "controlled by prat",
        ),
        (
            ["qwen", "--instructions-file", "missing", "--", "--append-system-prompt=other"],
            "controlled by prat",
        ),
    ],
)
def test_instruction_errors_precede_prompt_and_editor(
    flags: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt acquisition or editor reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main(["--json", "--edit", *flags]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid_arguments"
    assert message in result["error"]["message"]


@pytest.mark.parametrize("agent", ["claude", "qwen"])
def test_native_append_remains_supported_until_public_pair_is_active(agent: str) -> None:
    native = ("--append-system-prompt=legacy",)
    resolved = ResolvedProfile(BY_NAME[agent], None, ("fake",), Options(native_args=native))
    ADAPTERS[agent].validate(resolved)
    assert ADAPTERS[agent].build(resolved, b"task").argv[-1] == native[0]
    for options in (
        Options(instructions="rules", native_args=native),
        Options(instructions_file="missing", native_args=native),
    ):
        with pytest.raises(PratError, match="controlled by prat"):
            ADAPTERS[agent].validate(ResolvedProfile(BY_NAME[agent], None, ("fake",), options))


def test_instruction_config_listing_never_reads_files_and_selected_override_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".pratfile"
    config.write_text(
        'version=1\n[defaults]\ninstructions_file="missing"\n[profiles.work]\nagent="codex"\n'
    )
    assert main(["config", "validate"]) == 0
    capsys.readouterr()
    assert main(["profiles", "--json"]) == 0
    options = json.loads(capsys.readouterr().out)["profiles"][0]["options"]
    assert options["instructions_file"] == str(tmp_path / "missing")
    assert options["instructions"] is None
    assert main(["work", "--instructions", "override", "task", "--dry-run", "--json"]) == 0
    argv = json.loads(capsys.readouterr().out)["argv"]
    assert tomllib.loads(argv[4]) == {"developer_instructions": "override"}
    assert main(["work", "task", "--dry-run", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{config}: defaults.instructions_file" in error["message"]


def test_selected_instruction_file_resolves_symlink_parent_from_invocation_not_run_cwd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "actual" / "child"
    target.mkdir(parents=True)
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    (target.parent / "rules").write_bytes(b"actual\r\nrules\n")
    (tmp_path / "rules").write_text("wrong")
    assert (
        main(
            [
                "qwen",
                "--instructions-file",
                "link/../rules",
                "--cwd",
                str(target),
                "task",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["argv"][-1] == "--append-system-prompt=actual\r\nrules\n"
    assert result["cwd"] == str(target)


def test_instruction_capability_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {item["name"] for item in records if item["capabilities"]["instructions"]} == {
        "claude",
        "codex",
        "qwen",
        "droid",
    }


def test_instruction_reader_stops_after_limit_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large"
    path.write_bytes(b"a" * (INSTRUCTIONS_LIMIT + 100))
    original = os.read
    total = 0

    def measured(descriptor: int, count: int) -> bytes:
        nonlocal total
        data = original(descriptor, count)
        total += len(data)
        return data

    monkeypatch.setattr(os, "read", measured)
    with pytest.raises(PratError, match="byte limit"):
        read_instructions(str(path), OptionOrigin("config.instructions_file"))
    assert total == INSTRUCTIONS_LIMIT + 1


def test_instruction_file_open_failure_has_original_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*args: object, **kwargs: object) -> int:
        raise PermissionError("test read denied")

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(PratError) as caught:
        read_instructions(
            str(tmp_path / "rules"), OptionOrigin("global.toml: defaults.instructions_file")
        )
    assert caught.value.code == "invalid_config"
    assert "global.toml: defaults.instructions_file: Cannot open instructions file" in str(
        caught.value
    )
    assert "test read denied" in str(caught.value)


def test_instruction_defaults_validate_unselected_profiles_without_reading_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> str:
        pytest.fail("instruction file read during capability validation")

    monkeypatch.setattr(dispatch, "read_instructions", forbidden)
    path = tmp_path / ".pratfile"
    path.write_text(
        'version=1\n[defaults]\ninstructions_file="missing"\n[profiles.other]\nagent="gemini"\n'
    )
    assert main(["codex", "--instructions", "override", "task", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{path}: defaults.instructions_file" in error["message"]
    assert "does not support appended instructions" in error["message"]


def test_selected_config_instruction_file_uses_its_defining_directory_and_keeps_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\ninstructions_file="rules"\n')
    (global_path.parent / "rules").write_text("global rules")
    local = tmp_path / "nested" / "config.toml"
    local.parent.mkdir()
    local.write_text(
        'version=1\n[profiles.work]\nagent="qwen"\n'
        '[profiles.unused]\nagent="codex"\ninstructions_file="missing"\n'
    )
    assert main(["--config", str(local), "work", "task", "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["argv"][-1] == "--append-system-prompt=global rules"
    config = load_config(local)
    assert config.defaults.instructions is None
    assert config.defaults.instructions_file == str(global_path.parent / "rules")


@pytest.mark.parametrize(
    "agent, native",
    [
        ("claude", ("--append-system-prompt", "native")),
        ("claude", ("--append-system-prompt-file=rules",)),
        ("qwen", ("--append-system-prompt", "native")),
        ("droid", ("--append-system-prompt=native",)),
        ("droid", ("--append-system-prompt-file", "rules")),
        ("codex", ("-cdeveloper_instructions=other",)),
        ("codex", ("--config=developer_instructions=other",)),
    ],
)
def test_all_native_instruction_forms_conflict_with_active_public_setting(
    agent: str, native: tuple[str, ...]
) -> None:
    resolved = ResolvedProfile(
        BY_NAME[agent], None, ("fake",), Options(instructions_file="missing", native_args=native)
    )
    with pytest.raises(PratError, match="controlled by prat"):
        ADAPTERS[agent].validate(resolved)
