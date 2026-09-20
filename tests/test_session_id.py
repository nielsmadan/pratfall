import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import codex_stream, codex_usage, copilot_result
from pratfall.adapters import claude, codex, copilot, cursor, grok
from pratfall.catalog import BY_NAME
from pratfall.cli import dispatch, main
from pratfall.config import config_path, load_config, option_origins, parse_options, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options

SESSION = "4ebf82be-4b4b-4642-9e5a-654c4cd58642"


def test_session_id_fake_receives_the_exact_literal_argv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    log = tmp_path / "argv.json"
    native_output = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "session_id": SESSION,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({native_output!r})\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.claude]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main(["claude", "task", f"--session-id={SESSION}", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["native_session_id"] == SESSION
    assert json.loads(log.read_text()) == [
        "-p",
        "--output-format",
        "json",
        f"--session-id={SESSION}",
    ]


def test_requested_session_survives_a_run_that_never_reports_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    script.write_text("import sys\nsys.stdin.buffer.read()\nsys.exit(9)\n")
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.claude]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main(["claude", "task", f"--session-id={SESSION}", "--json"]) == 9
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["native_session_id"] == SESSION


def test_a_wedged_streaming_run_still_names_the_session_it_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case the feature exists for: the run never finishes, so the only way back to
    the native transcript is the id the agent announced before it wedged."""
    script = tmp_path / "native.py"
    started = json.dumps({"type": "thread.started", "thread_id": "wedged-thread"})
    script.write_text(
        "import sys, time\n"
        f"sys.stdout.write({started!r} + chr(10))\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.codex]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main(["codex", "task", "--timeout", "1", "--json"]) == 124
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["native_session_id"] == "wedged-thread"


def test_reported_session_outranks_the_requested_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    native_output = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "session_id": "actual-session",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    script.write_text(f"import sys\nsys.stdin.buffer.read()\nprint({native_output!r})\n")
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.claude]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main(["claude", "task", f"--session-id={SESSION}", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["native_session_id"] == "actual-session"


def test_session_id_four_layer_precedence_and_provenance(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text('version=1\n[defaults]\nsession_id="global"\n')
    assert resolve_profile(load_config(), "claude").options.session_id == "global"
    local = tmp_path / ".pratfile"
    local.write_text(
        'version=1\n[defaults]\nsession_id="local"\n'
        '[profiles.work]\nagent="claude"\nsession_id="profile"\n'
    )
    config = load_config()
    assert resolve_profile(config, "claude").options.session_id == "local"
    assert resolve_profile(config, "work").options.session_id == "profile"
    resolved = resolve_profile(config, "work", Options(session_id="cli"))
    assert resolved.options.session_id == "cli"
    origin = option_origins(config, "work", Options(session_id="cli"))["session_id"]
    assert origin.label == "command line: selector 'work'.session_id"
    assert origin.code == "invalid_arguments"


@pytest.mark.parametrize("value", [None, True, 1, [], "", " \t", "a\0b", "\ud800"])
def test_session_id_config_validation(value: object) -> None:
    with pytest.raises(PratError, match=r"defaults\.session_id") as caught:
        parse_options({"session_id": value}, "config.toml: defaults")
    assert caught.value.code == "invalid_config"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "expected a nonempty session id without NUL bytes."),
        (" \t", "expected a nonempty session id without NUL bytes."),
        ("a\0b", "expected a nonempty session id without NUL bytes."),
        ("\ud800", "session id must be valid UTF-8."),
    ],
)
def test_invalid_session_id_rejected_before_input_or_editor(
    value: str, message: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt/editor must not be reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main(["claude", "--session-id", value, "--edit", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert error["message"] == f"--session-id: {message}"


@pytest.mark.parametrize("agent", ["codex", "opencode", "qwen"])
def test_unsupported_session_id_rejected_before_input_or_editor(
    agent: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("prompt/editor must not be reached")

    monkeypatch.setattr(dispatch, "acquire_prompt", forbidden)
    monkeypatch.setattr(dispatch, "edit_prompt", forbidden)
    assert main([agent, f"--session-id={SESSION}", "--file=missing", "--edit", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "does not support a session id" in error["message"]


@pytest.mark.parametrize("native", [["--session-id=other"], ["--session-id", "other"]])
def test_native_session_id_stays_reserved(
    native: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["claude", "task", "--json", "--", *native]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_arguments"
    assert "controlled by prat" in error["message"]


def test_session_id_inventory_and_profile_listing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {r["name"] for r in records if r["capabilities"]["session_id"]} == REQUESTING
    (tmp_path / ".pratfile").write_text(
        f'version=1\n[profiles.run]\nagent="claude"\nsession_id="{SESSION}"\n'
    )
    assert main(["profiles", "--json"]) == 0
    profile = json.loads(capsys.readouterr().out)["profiles"][0]
    assert profile["options"]["session_id"] == SESSION


# Reporting is free wherever the native protocol already carries an id. Requesting forwards a
# native flag, so it is granted only where that flag names a NEW session rather than resuming
# one. Each agent's verified quote and date live in docs/reference/<agent>.md; amp, cortex,
# crush, iflow and warp are unverified rather than excluded.
REQUESTING = {name for name, spec in BY_NAME.items() if spec.capabilities.session_id}
REPORTING = {name for name, spec in BY_NAME.items() if spec.capabilities.reports_session_id}


def test_the_inventory_declares_both_sides_of_the_feature(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["agents", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)["agents"]
    assert {r["name"] for r in records if r["capabilities"]["session_id"]} == {
        "claude",
        "copilot",
        "grok",
    }
    assert {r["name"] for r in records if r["capabilities"]["reports_session_id"]} == {
        "claude",
        "codex",
        "copilot",
        "cursor",
        "grok",
    }
    assert REQUESTING <= REPORTING


@pytest.mark.parametrize("agent", sorted(REPORTING))
def test_every_reporting_agent_surfaces_its_session(agent: str) -> None:
    documents = {
        "claude": lambda: claude.decode(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "answer",
                    "session_id": "native-session",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )
        ),
        "codex": lambda: codex.decode(
            codex_stream(
                {"type": "thread.started", "thread_id": "native-session"},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"id": "i1", "type": "agent_message", "text": "answer"},
                },
                {"type": "turn.completed", "usage": codex_usage()},
            )
        ),
        "copilot": lambda: copilot.decode(
            codex_stream(
                {"type": "assistant.message", "data": {"messageId": "1", "content": "answer"}},
                copilot_result() | {"sessionId": "native-session"},
            )
        ),
        "cursor": lambda: cursor.decode(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "answer",
                    "duration_ms": 1234,
                    "session_id": "native-session",
                }
            )
        ),
        "grok": lambda: grok.decode(
            json.dumps(
                {
                    "text": "answer",
                    "stopReason": "end_turn",
                    "sessionId": "native-session",
                    "requestId": "request",
                }
            )
        ),
    }
    decoded = documents[agent]()
    assert decoded.error is None
    assert decoded.output == "answer"
    assert decoded.session_id == "native-session"


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("claude", ["-p", "--output-format", "json", f"--session-id={SESSION}"]),
        (
            "copilot",
            ["--output-format=json", f"--session-id={SESSION}", "--prompt=task"],
        ),
        (
            "grok",
            [
                "--no-auto-update",
                "--output-format",
                "json",
                "--session-id",
                SESSION,
                "--single=task",
            ],
        ),
    ],
)
def test_every_requesting_agent_forwards_its_native_flag(
    agent: str, expected: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([agent, "task", f"--session-id={SESSION}", "--json", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["argv"] == [agent, *expected]


SURROGATE = "sess-\ud800-x"


@pytest.mark.parametrize(
    ("agent", "document"),
    [
        (
            "claude",
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "answer",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ),
        (
            "cursor",
            {"type": "result", "subtype": "success", "is_error": False, "result": "answer"},
        ),
        ("grok", {"text": "answer", "stopReason": "end_turn", "requestId": "request"}),
    ],
)
@pytest.mark.parametrize("reported", [SURROGATE, "", "   ", "a\0b"])
def test_an_unusable_reported_session_is_dropped_not_emitted(
    agent: str, document: dict[str, object], reported: str
) -> None:
    """A session id that cannot survive `json.dumps(..., ensure_ascii=False)` + print must
    never reach the envelope: it destroys the whole result at the output boundary."""
    key = "sessionId" if agent == "grok" else "session_id"
    decoder = {"claude": claude, "cursor": cursor, "grok": grok}[agent]
    decoded = decoder.decode(json.dumps(document | {key: reported}))
    assert decoded.session_id is None
    json.dumps(decoded.session_id, ensure_ascii=False).encode("utf-8")


def test_codex_schema_reports_the_thread_like_ordinary_decoding() -> None:
    stream = codex_stream(
        {"type": "thread.started", "thread_id": "native-session"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "i1", "type": "agent_message", "text": '{"a": 1}'},
        },
        {"type": "turn.completed", "usage": codex_usage()},
    )
    assert codex.decode(stream).session_id == "native-session"
    assert codex.decode_schema(stream).session_id == "native-session"


def test_a_blank_reported_session_never_outranks_the_requested_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "native.py"
    native_output = json.dumps(
        {"text": "answer", "stopReason": "end_turn", "sessionId": "   ", "requestId": "r"}
    )
    script.write_text(f"import sys\nsys.stdin.buffer.read()\nprint({native_output!r})\n")
    (tmp_path / ".pratfile").write_text(
        f"version=1\n[agents.grok]\ncommand={json.dumps([sys.executable, str(script)])}\n"
    )
    assert main(["grok", "task", f"--session-id={SESSION}", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["native_session_id"] == SESSION
