import json
import sys
from pathlib import Path

import pytest

from pratfall.adapters import openhands
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, ResultError


def message(text: str = "answer") -> dict[str, object]:
    return {
        "kind": "MessageEvent",
        "id": "answer",
        "source": "agent",
        "llm_message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def finish(text: str = "done") -> dict[str, object]:
    return {
        "kind": "ActionEvent",
        "source": "agent",
        "tool_name": "finish",
        "action": {"kind": "FinishAction", "message": text},
    }


def stream(*events: object) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def test_openhands_builder_keeps_literal_prefix_and_equals_prompt() -> None:
    resolved = ResolvedProfile(
        BY_NAME["openhands"],
        None,
        ("wrapper", "literal $HOME"),
        Options(native_args=("--override-with-envs",)),
    )
    invocation = openhands.build(resolved, "-雪\n$(touch /tmp/never)\n".encode())
    assert invocation.argv == (
        "wrapper",
        "literal $HOME",
        "--headless",
        "--json",
        "--override-with-envs",
        "--task=-雪\n$(touch /tmp/never)\n",
    )
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "arguments",
    [
        ("--task=other",),
        ("-tother",),
        ("--file", "-"),
        ("--headless",),
        ("--json",),
        ("--resume=x",),
        ("--last",),
        ("--model=x",),
        ("--config=x",),
        ("--cwd=x",),
        ("--yolo",),
        ("--executor=x",),
        ("cloud",),
        ("serve",),
        ("--unknown",),
        ("@args",),
        ("--override-with-envs=true",),
    ],
)
def test_openhands_rejects_reserved_and_unknown_native_arguments(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(PratError) as failure:
        openhands.validate(arguments)
    assert failure.value.code == "invalid_arguments"


@pytest.mark.parametrize(
    "options",
    [
        Options(model="x"),
        Options(effort="high"),
        Options(fast=False),
        Options(max_turns=2),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_openhands_rejects_unsupported_controls(
    native_contract_config: Path, options: Options
) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "oh", options)


@pytest.mark.parametrize("selector", ["oh", "openhands"])
def test_openhands_cli_satisfies_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        ["--config", str(native_contract_config), selector, "--json", f"--prompt={prompt}"]
    )
    result = json.loads(capsys.readouterr().out)
    native = json.loads(result["output"])
    assert status == result["exit_code"] == result["native_exit_code"] == 0
    assert result["agent"] == "openhands" and result["status"] == "success"
    assert native["argv"] == ["--headless", "--json", f"--task={prompt}"]
    assert native["stdin"] == "" and native["prompt"] == prompt
    assert (result["usage"], result["reported_models"], result["cost_usd"]) == (None, None, None)


@pytest.mark.parametrize("event", [message("café 雪"), finish("café 雪")])
def test_openhands_mixed_framing_every_byte_boundary(event: object) -> None:
    payload = (
        "Agent is working\r\n"
        + stream(
            {"kind": "SystemPromptEvent"},
            {
                "kind": "MessageEvent",
                "source": "user",
                "llm_message": {"role": "user", "content": [{"type": "text", "text": "request"}]},
            },
            {"kind": "AgentErrorEvent"},
            event,
        )
        + "Agent finished\r\n\n"
        "──────── CONVERSATION SUMMARY ────────\nNumber of agent messages: 1\n"
        "Last message sent by the agent:\n╭─ Agent ─╮\n│ café 雪 │\n╰─────────╯\n"
        + stream({"kind": "ConversationErrorEvent", "code": "echoed", "detail": "panel text"})
    )
    incremental = openhands.consumer()
    for byte in payload.encode():
        incremental.feed(bytes((byte,)))
    decoded = incremental.finish()
    assert decoded.output == "café 雪" and decoded.error is None
    assert decoded.usage is None
    assert decoded.reported_models is None
    assert decoded.cost_usd is None


def test_openhands_text_blocks_are_concatenated_without_reasoning_or_images() -> None:
    event = {
        "kind": "MessageEvent",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "reasoning_content": "private thought",
            "content": [
                {"type": "text", "text": "one"},
                {"type": "image", "image_urls": []},
                {"type": "text", "text": " two"},
            ],
        },
    }
    decoded = openhands.decode(stream(event).rstrip())
    assert decoded.output == "one two" and decoded.error is None


@pytest.mark.parametrize(
    ("content", "reasoning"),
    [
        ([], None),
        ([], "reasoning only"),
        ([{"type": "text", "text": " \n"}], None),
        ([{"type": "image", "image_urls": []}], None),
    ],
    ids=["empty", "reasoning-only", "blank-text", "image-only"],
)
@pytest.mark.parametrize("terminal", [message("recovered"), finish("recovered"), None])
def test_openhands_nonterminal_messages_require_later_completion(
    content: list[dict[str, object]], reasoning: str | None, terminal: object | None
) -> None:
    payload = stream(
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
            },
        },
        {
            "kind": "MessageEvent",
            "source": "user",
            "llm_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Continue working on the request."}],
            },
        },
    )
    if terminal is not None:
        decoded = openhands.decode(payload + stream(terminal))
        assert decoded.output == "recovered" and decoded.error is None
    else:
        decoded = openhands.decode(payload + "Agent finished\n")
        assert decoded.output == ""
        assert decoded.error == ResultError(
            "protocol_error", "OpenHands stream ended without a terminal assistant event."
        )


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "Agent finished\n",
        "Agent is working\nAgent finished\n",
        "──── CONVERSATION SUMMARY ────\n" + stream(message()),
        stream({"kind": "ActionEvent", "source": "agent", "tool_name": "terminal", "action": None}),
        stream({"kind": "MessageEvent", "source": "user", "llm_message": {"role": "user"}}),
        stream(message("  \n")),
        stream({"kind": "FutureEvent"}),
        "unexpected prose\n",
        "{}\n",
        "[]\n",
        "{broken\n",
        "[" * 2000 + "]" * 2000,
        '{"kind":"TokenEvent","value":' + "1" * 129 + "}\n",
        '{"kind":"TokenEvent","value":NaN}\n',
        '{"kind":"TokenEvent","value":1e999}\n',
    ],
)
def test_openhands_requires_valid_terminal_evidence(payload: str) -> None:
    decoded = openhands.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "MessageEvent", "source": [], "llm_message": {}},
        {"kind": "MessageEvent", "source": "agent", "llm_message": []},
        {"kind": "MessageEvent", "source": "agent", "llm_message": {"role": "user", "content": []}},
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": None},
        },
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": [5]},
        },
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": [{"type": []}]},
        },
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": [{"type": "text", "text": 7}]},
        },
        {"kind": "ActionEvent", "action": []},
        {"kind": "ActionEvent", "source": "agent", "tool_name": "finish", "action": None},
        {
            "kind": "ActionEvent",
            "source": "user",
            "tool_name": "finish",
            "action": {"kind": "FinishAction", "message": "x"},
        },
        {"kind": "ConversationErrorEvent", "code": [], "detail": 5},
    ],
)
@pytest.mark.parametrize("before_terminal", [False, True])
def test_openhands_malformed_records_retain_terminal_text(
    event: object, before_terminal: bool
) -> None:
    events = (event, message("prior")) if before_terminal else (message("prior"), event)
    decoded = openhands.decode(stream(*events))
    assert decoded.output == "prior"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_openhands_duplicate_terminal_and_late_provider_error() -> None:
    events = stream(message("first"), finish("duplicate"))
    duplicate = openhands.decode(events)
    assert duplicate.output == "first"
    assert duplicate.error == ResultError(
        "protocol_error", "OpenHands emitted more than one terminal assistant event."
    )
    failed = openhands.decode(
        events
        + stream(
            {"kind": "ConversationErrorEvent", "code": "APIError", "detail": "provider failed"}
        )
    )
    assert failed.output == "first" and failed.error == ResultError(
        "provider_error", "provider failed"
    )


@pytest.mark.parametrize(
    ("code", "detail", "expected"),
    [
        ("APIError", "", "APIError"),
        ("", "", "OpenHands reported a conversation error."),
    ],
)
def test_openhands_provider_error_fallback(code: str, detail: str, expected: str) -> None:
    decoded = openhands.decode(
        stream({"kind": "ConversationErrorEvent", "code": code, "detail": detail})
    )
    assert decoded.error == ResultError("provider_error", expected)


@pytest.mark.parametrize("payload", [stream(message("\ud800")), stream(finish("\udfff"))])
def test_openhands_invalid_unicode_is_normalized(payload: str) -> None:
    decoded = openhands.decode(payload)
    assert decoded.error is not None and decoded.error.code == "output_encoding"


@pytest.mark.parametrize("suffix", [b"x" * 513, b"x" * 513 + b"\n", b"\xff\n"])
def test_openhands_framing_failure_preserves_answer(suffix: bytes) -> None:
    incremental = openhands.consumer(ConsumerLimits(event_bytes=512))
    incremental.feed(stream(message("prior")).encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(suffix)
    assert failure.value.error.code in {"stdout_limit_exceeded", "output_encoding"}
    assert incremental.feed(b"ignored after failure") is None
    with pytest.raises(ConsumerFailure) as completed:
        incremental.finish()
    assert completed.value.error == failure.value.error
    assert completed.value.decoded is not None and completed.value.decoded.output == "prior"


def test_openhands_state_and_terminal_record_limits() -> None:
    for limits in (ConsumerLimits(state_bytes=3), ConsumerLimits(records=0)):
        incremental = openhands.consumer(limits)
        with pytest.raises(ConsumerFailure) as failure:
            incremental.feed(stream(message("answer")).encode())
        assert failure.value.error.code == "stdout_limit_exceeded"
    incomplete = openhands.consumer(ConsumerLimits(state_bytes=3))
    with pytest.raises(ConsumerFailure) as failure:
        incomplete.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"


def test_openhands_summary_lines_remain_bounded_and_utf8_checked() -> None:
    incremental = openhands.consumer(ConsumerLimits(event_bytes=128))
    incremental.feed("── CONVERSATION SUMMARY ──\n".encode())
    with pytest.raises(ConsumerFailure) as failure:
        incremental.feed(b"x" * 129)
    assert failure.value.error.code == "stdout_limit_exceeded"


def test_openhands_finish_consumes_final_record_and_is_single_use() -> None:
    incremental = openhands.consumer()
    incremental.feed(stream(finish("")).encode().rstrip(b"\n"))
    assert incremental.finish().error is None
    with pytest.raises(RuntimeError, match="already finished"):
        incremental.finish()
    with pytest.raises(RuntimeError, match="already finished"):
        incremental.feed(b"")


@pytest.mark.parametrize("native_exit", [0, 17])
def test_openhands_conversation_error_through_real_runner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    payload = stream(
        message("partial"),
        {"kind": "ConversationErrorEvent", "code": "APIError", "detail": "failed"},
    )
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.openhands]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "oh", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == result["exit_code"] == (1 if native_exit == 0 else native_exit)
    assert result["native_exit_code"] == native_exit and result["output"] == "partial"
    assert result["status"] == "error"
    assert result["error"]["code"] == ("provider_error" if native_exit == 0 else "native_exit")


def test_openhands_native_startup_and_wrapped_model_banner() -> None:
    payload = (
        "OpenHands CLI terminal UI may not work correctly in this environment: "
        "Rich detected a non-interactive or unsupported terminal; interactive UI may not render correctly\n"
        "To override Rich's detection, you can set TTY_INTERACTIVE=1 (and optionally TTY_COMPATIBLE=1).\n"
        "Initializing agent...\n✓ Hooks loaded\n✓ Agent initialized with model: \n"
        "provider/a-long-native-model-name\n"
        "Agent is working\n"
        + stream(message("answer"))
        + "Agent finished\n──── CONVERSATION SUMMARY ────\nGoodbye! 👋\n"
        "Conversation ID: 12345678123456781234567812345678\n"
        "Hint: run openhands --resume 12345678-1234-5678-1234-567812345678\n"
        "to resume this conversation.\n"
    )
    decoded = openhands.decode(payload)
    assert decoded.output == "answer" and decoded.error is None
    assert decoded.reported_models is None


@pytest.mark.parametrize("following", ["Agent is working\nunknown text\n", "{broken json\n"])
def test_openhands_model_banner_does_not_hide_malformed_protocol(following: str) -> None:
    decoded = openhands.decode(
        "✓ Agent initialized with model: model\n" + following + stream(message())
    )
    assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"
