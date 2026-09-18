import io
import json
import os
import sys
from pathlib import Path

import pytest

from adapter_helpers import copilot_result
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, option_origins, parse_options, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options

PREFIXES = {
    "codex": ["exec", "--json"],
    "hermes": ["chat", "--oneshot", "--quiet", "--query-file", "-"],
    "copilot": ["--output-format=json"],
    "opencode": ["run", "--format", "json"],
}
FLAGS = {"codex": "--image", "hermes": "--image", "copilot": "--attachment", "opencode": "--file"}


def _suffix(agent: str, prompt: str) -> list[str]:
    if agent == "codex":
        return ["--", "-"]
    return [f"--prompt={prompt}"] if agent == "copilot" else []


@pytest.mark.parametrize(
    ("agent", "name"),
    [
        (agent, name)
        for agent in PREFIXES
        for name in ("binary image.png", "-image.png", "image,part.png", "image.png ")
        if agent != "codex" or "," not in name
    ],
)
def test_attachment_fake_receives_binary_file_path_and_prompt(
    agent: str, name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attachment = tmp_path / name
    attachment.write_bytes(b"\xff\x00\x80\xfe")
    second = tmp_path / "second image.png"
    second.write_bytes(b"\x00other")
    paths = [attachment] if agent == "hermes" else [attachment, second, attachment]
    script = tmp_path / "native.py"
    log = tmp_path / "argv.json"
    outputs = {
        "codex": "\n".join(
            [
                '{"type":"item.completed","item":{"id":"1","type":"agent_message","text":"ok"}}',
                '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1,"cached_input_tokens":0}}',
            ]
        ),
        "hermes": "ok",
        "copilot": json.dumps(
            {"type": "assistant.message", "data": {"messageId": "1", "content": "ok"}}
        )
        + "\n"
        + json.dumps(copilot_result()),
        "opencode": "\n".join(
            [
                '{"type":"text","part":{"type":"text","id":"1","text":"ok","time":{"end":1}}}',
                '{"type":"step_finish","part":{"type":"step-finish","id":"2","reason":"stop","tokens":{"input":1,"output":1,"reasoning":0,"cache":{"read":0,"write":0}}}}',
            ]
        ),
    }
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps([sys.argv[1:], sys.stdin.read()]))\n"
        f"print({outputs[agent]!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.{agent}]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main([agent, "task", *[f"--attach={p.name}" for p in paths], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    argv, stdin = json.loads(log.read_text())
    assert argv == [
        *PREFIXES[agent],
        *[f"{FLAGS[agent]}={p}" for p in paths],
        *_suffix(agent, "task"),
    ]
    assert stdin == ("" if agent == "copilot" else "task")
    assert attachment.read_bytes() == b"\xff\x00\x80\xfe"


def test_attachments_four_layer_path_origins_and_clearing(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\nattachments=["global"]\n')
    config = load_config()
    assert resolve_profile(config, "codex").options.attachments == (
        str(global_path.parent / "global"),
    )
    local = tmp_path / "nested" / "config.toml"
    local.parent.mkdir()
    local.write_text(
        'version=1\n[defaults]\nattachments=["local"]\n'
        '[profiles.work]\nagent="codex"\nattachments=["profile", "profile"]\n'
        '[profiles.clear]\nagent="claude"\nattachments=[]\n'
    )
    config = load_config(local)
    assert resolve_profile(config, "codex").options.attachments == (str(local.parent / "local"),)
    assert (
        resolve_profile(config, "work").options.attachments == (str(local.parent / "profile"),) * 2
    )
    assert (
        option_origins(config, "work")["attachments"].label == f"{local}: profiles.work.attachments"
    )
    assert resolve_profile(config, "work", Options(attachments=("/cli",))).options.attachments == (
        "/cli",
    )
    assert resolve_profile(config, "clear").options.attachments == ()
    assert resolve_profile(config, "work", Options(attachments=())).options.attachments == ()
    local.write_text('version=1\n[profiles.work]\nagent="codex"\n')
    assert resolve_profile(load_config(local), "work").options.attachments == (
        str(global_path.parent / "global"),
    )


@pytest.mark.parametrize("agent", PREFIXES)
@pytest.mark.parametrize("from_config", [False, True])
def test_selected_attachment_preserves_symlink_parent_target_and_origin(
    agent: str, from_config: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    actual = tmp_path / "actual"
    (actual / "child").mkdir(parents=True)
    (actual / "image.png").write_bytes(b"\xff")
    base = config_dir if from_config else tmp_path
    (base / "link").symlink_to(actual / "child", target_is_directory=True)
    (base / "image.png").write_text("wrong lexical target")
    config = config_dir / "chosen.toml"
    config.write_text(
        f'version=1\n[profiles.work]\nagent="{agent}"\n'
        + ('attachments=["link/../image.png"]\n' if from_config else "")
    )
    cwd = tmp_path / "run"
    cwd.mkdir()
    args = [] if from_config else ["--attach", "link/../image.png"]
    assert (
        main(
            [
                "work",
                "task",
                "--config",
                str(config),
                "--cwd",
                str(cwd),
                *args,
                "--json",
                "--dry-run",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["cwd"] == str(cwd)
    assert preview["argv"] == [
        agent,
        *PREFIXES[agent],
        f"{FLAGS[agent]}={actual / 'image.png'}",
        *_suffix(agent, "task"),
    ]


@pytest.mark.parametrize("agent", ["claude", "qwen", "vibe"])
def test_unsupported_attachments_fail_without_resource_or_input_reads(
    agent: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("must reject before attachment, prompt, context, or editor access")

    monkeypatch.setattr(dispatch, "prepare_attachments", forbidden)
    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main([agent, "--attach=missing", "--file=missing", "--edit", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "does not support attachments" in error["message"]


@pytest.mark.parametrize(
    "case", ["missing", "directory", "fifo", "unreadable", "multiple", "comma", "canonical-comma"]
)
def test_invalid_attachment_fails_before_input(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt/editor must not be reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    path = tmp_path / "image.png"
    expected = "cannot read attachment"
    agent = "codex"
    extras: list[str] = []
    if case == "directory":
        path.mkdir()
        expected = "not a regular attachment file"
    elif case == "fifo":
        os.mkfifo(path)
        expected = "not a regular attachment file"
    elif case == "unreadable":
        path.write_bytes(b"image")
        path.chmod(0)
    elif case == "multiple":
        agent = "hermes"
        extras = ["--attach=second-missing"]
        expected = "at most 1 attachment"
    elif case in {"comma", "canonical-comma"}:
        target = tmp_path / "image,part.png"
        target.write_bytes(b"image")
        if case == "canonical-comma":
            path.symlink_to(target)
        else:
            path = target
        expected = "cannot contain commas"
    try:
        assert main([agent, f"--attach={path}", *extras, "--edit", "--json"]) == 2
        error = json.loads(capsys.readouterr().out)["error"]
        assert error["code"] == "invalid_arguments"
        assert "attachments" in error["message"]
        assert expected in error["message"]
    finally:
        if case == "unreadable":
            path.chmod(0o600)


@pytest.mark.parametrize(
    ("agent", "native"),
    [
        ("codex", ["--image=x"]),
        ("codex", ["-i", "x"]),
        ("codex", ["-ix"]),
        ("hermes", ["--image", "x"]),
        ("copilot", ["--attachment=x"]),
        ("opencode", ["--file=x"]),
        ("opencode", ["-f", "x"]),
        ("opencode", ["-fx"]),
    ],
)
def test_attachment_collision_and_native_only_compatibility(
    agent: str,
    native: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main([agent, "task", "--dry-run", "--json", "--", *native]) == 0
    preview = json.loads(capsys.readouterr().out)
    suffix = ["-"] if agent == "codex" else _suffix(agent, "task")
    assert preview["argv"] == [agent, *PREFIXES[agent], *native, *suffix]

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("must reject before resource or prompt reads")

    monkeypatch.setattr(dispatch, "prepare_attachments", forbidden)
    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    assert main([agent, "--attach=missing", "--json", "--", *native]) == 2
    assert "controlled by prat" in json.loads(capsys.readouterr().out)["error"]["message"]


@pytest.mark.parametrize("value", [None, True, 1, "path", [""], [" "], [1], ["a\0b"]])
def test_attachment_config_shape_validation(value: object) -> None:
    with pytest.raises(PratError, match=r"defaults\.attachments"):
        parse_options({"attachments": value}, "config.toml: defaults")


def test_attachment_listing_does_not_read_files_and_defaults_validate_unused_profiles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".pratfile"
    config.write_text('version=1\n[profiles.work]\nagent="codex"\nattachments=["missing"]\n')

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("listing and config validation must not read attachments")

    monkeypatch.setattr(dispatch, "prepare_attachments", forbidden)
    assert main(["config", "validate", "--json"]) == 0
    capsys.readouterr()
    assert main(["profiles", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["profiles"][0]["options"]["attachments"] == [
        str(tmp_path / "missing")
    ]
    assert main(["profiles"]) == 0
    assert f'attachments=["{tmp_path / "missing"}"]' in capsys.readouterr().out
    config.write_text(
        'version=1\n[defaults]\nattachments=["missing"]\n[profiles.unused]\nagent="claude"\n'
    )
    assert main(["codex", "task", "--json", "--dry-run"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{config}: defaults.attachments" in error["message"]


def test_attachment_requires_prompt_and_preserves_stdin_and_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "image.png").write_bytes(b"\xff")
    common = ["copilot", "--attach=image.png", "--json", "--dry-run"]
    assert main(common) == 2
    assert "Provide exactly one prompt" in json.loads(capsys.readouterr().out)["error"]["message"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin task"))
    assert main(common) == 0
    assert json.loads(capsys.readouterr().out)["argv"][-1] == "--prompt=stdin task"
    monkeypatch.setattr(sys, "stdin", io.StringIO("context"))
    (tmp_path / ".pratfile").write_text('version=1\n[templates.work]\nprompt="Review $input"\n')
    assert main([*common, "--template=work", "task"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"][-1] == "--prompt=Review context\n\ntask"


def test_attachment_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {
        r["name"]: (
            r["capabilities"]["attachment_types"],
            r["capabilities"]["attachment_max_count"],
        )
        for r in records
        if r["capabilities"]["attachments"]
    } == {
        "codex": (["image"], None),
        "hermes": (["image"], 1),
        "copilot": (["image", "native document"], None),
        "opencode": (["file"], None),
        "pi": (["image", "text file"], None),
    }
    assert main(["agents"]) == 0
    assert "attachment_types=image, attachment_max_count=1" in capsys.readouterr().out


def test_attachment_editor_task_and_later_native_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"before")
    log = tmp_path / "observed.json"
    script = tmp_path / "native.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "image = pathlib.Path(sys.argv[-1].split('=', 1)[1])\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps([sys.stdin.read(), list(image.read_bytes())]))\n"
        "print('ok')\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.hermes]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    drafts: list[bytes] = []

    def edit(draft: bytes, _cwd: Path) -> bytes:
        drafts.append(draft)
        image.write_bytes(b"\xff\x00after")
        return b"edited task"

    monkeypatch.setattr(dispatch, "edit_prompt", edit)
    assert main(["hermes", "--attach=image.png", "--edit", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == "ok"
    assert drafts == [b""]
    assert json.loads(log.read_text()) == ["edited task", list(b"\xff\x00after")]


def test_attachment_cli_list_replaces_missing_config_resources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".pratfile"
    config.write_text(
        'version=1\n[defaults]\nattachments=["missing-global"]\n'
        '[profiles.work]\nagent="codex"\nattachments=["missing-profile"]\n'
    )
    (tmp_path / "cli.png").write_bytes(b"\xff")
    assert main(["--attach", "cli.png", "work", "task", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"] == [
        "codex",
        "exec",
        "--json",
        f"--image={tmp_path / 'cli.png'}",
        "--",
        "-",
    ]
    config.write_text(
        'version=1\n[defaults]\nattachments=["missing-global"]\n'
        '[profiles.clear]\nagent="claude"\nattachments=[]\n'
    )
    assert main(["clear", "task", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"] == [
        "claude",
        "-p",
        "--output-format",
        "json",
    ]


def test_missing_config_attachment_retains_source_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".pratfile"
    config.write_text('version=1\n[profiles.work]\nagent="codex"\nattachments=["missing"]\n')
    assert main(["work", "task", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_config"
    assert f"{config}: profiles.work.attachments" in error["message"]


def test_attachment_dash_is_a_file_and_does_not_consume_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "-").write_bytes(b"\xff")
    monkeypatch.setattr(sys, "stdin", io.StringIO("task from stdin"))
    assert main(["copilot", "--attach=-", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"] == [
        "copilot",
        "--output-format=json",
        f"--attachment={tmp_path / '-'}",
        "--prompt=task from stdin",
    ]


def test_attachment_empty_config_preserves_native_only_image(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".pratfile").write_text(
        'version=1\n[profiles.work]\nagent="codex"\nattachments=[]\nnative_args=["-inative.png"]\n'
    )
    assert main(["work", "task", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["argv"] == [
        "codex",
        "exec",
        "--json",
        "-inative.png",
        "-",
    ]
