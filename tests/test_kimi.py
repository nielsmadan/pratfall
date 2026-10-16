import json
from pathlib import Path

import pytest

from adapter_helpers import resolved
from pratfall.adapters import kimi
from pratfall.cli import main
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options


def stream(*events: object) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def test_kimi_code_transports_literal_prompt_as_one_argv_value() -> None:
    prompt = "-雪\n$HOME `id` $(touch forbidden)\n"
    invocation = kimi.build(resolved("kimi", Options(model="kimi-k2")), prompt.encode())
    assert invocation.argv == (
        "kimi-wrapper",
        "native",
        "--output-format",
        "stream-json",
        "--model",
        "kimi-k2",
        f"--prompt={prompt}",
    )
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "flag",
    [
        "--print",
        "--input-format=text",
        "--final-message-only",
        "--thinking",
        "--no-thinking",
        "--plan",
        "--yolo",
        "--auto",
        "--resume=x",
        "--prompt=x",
        "--model=x",
        "--output-format=text",
    ],
)
def test_kimi_code_rejects_legacy_and_managed_flags(flag: str) -> None:
    with pytest.raises(PratError):
        kimi.validate(resolved("kimi", Options(native_args=(flag,))))


def test_kimi_code_handles_metadata_tools_goals_and_final_answer() -> None:
    payload = stream(
        {"role": "meta", "type": "system.version", "version": "2.1.1"},
        {"role": "assistant", "content": "Checking"},
        {
            "role": "assistant",
            "tool_calls": [
                {"type": "function", "id": "t", "function": {"name": "read", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "content": "tool output", "tool_call_id": "t"},
        {"role": "meta", "type": "turn.step.retrying", "error_message": "transient"},
        {"role": "assistant", "content": "Final 雪"},
        {"role": "assistant", "content": ""},
        {"type": "goal.summary", "status": "complete", "tokensUsed": 42},
        {"role": "meta", "type": "session.resume_hint", "session_id": "session"},
    )
    for split in range(len(payload.encode()) + 1):
        consumer = kimi.consumer()
        data = payload.encode()
        consumer.feed(data[:split])
        consumer.feed(data[split:])
        result = consumer.finish()
        assert result.output == "Final 雪"
        assert result.session_id == "session"
        assert result.error is None
        assert result.usage is None


@pytest.mark.parametrize(
    "event",
    [
        {"role": "assistant", "content": 5},
        {"role": "future"},
        {"role": "meta", "type": "unknown"},
        {"role": "meta", "type": []},
        {"role": "meta", "type": "session.resume_hint", "session_id": 1},
    ],
)
def test_kimi_code_retains_answer_after_malformed_record(event: object) -> None:
    result = kimi.decode(stream({"role": "assistant", "content": "partial"}, event))
    assert result.output == "partial"
    assert result.error is not None and result.error.code == "protocol_error"


def test_kimi_code_state_overflow_retains_previous_answer() -> None:
    consumer = kimi.consumer(ConsumerLimits(state_bytes=7))
    consumer.feed(stream({"role": "assistant", "content": "partial"}).encode())
    with pytest.raises(ConsumerFailure):
        consumer.feed(stream({"role": "assistant", "content": "longer answer"}).encode())
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.decoded is not None
    assert failure.value.decoded.output == "partial"


def test_kimi_code_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = "-雪\n$HOME $(touch forbidden)\n"
    assert (
        main(["--config", str(native_contract_config), "kimi", f"--prompt={prompt}", "--json"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    record = json.loads(result["output"])
    assert result["status"] == "success"
    assert record["argv"] == ["--output-format", "stream-json", f"--prompt={prompt}"]
    assert record["stdin"] == ""
    assert record["prompt"] == prompt


def test_kimi_session_replacement_refunds_combined_retention_budget() -> None:
    consumer = kimi.consumer(ConsumerLimits(state_bytes=10))
    consumer.feed(stream({"role": "assistant", "content": "answer"}).encode())
    consumer.feed(
        stream({"role": "meta", "type": "session.resume_hint", "session_id": "long"}).encode()
    )
    consumer.feed(
        stream({"role": "meta", "type": "session.resume_hint", "session_id": "s"}).encode()
    )
    consumer.feed(stream({"role": "assistant", "content": "123456789"}).encode())
    result = consumer.finish()
    assert result.output == "123456789"
    assert result.session_id == "s"
    assert result.error is None


def test_kimi_session_overflow_preserves_previous_answer_and_session() -> None:
    consumer = kimi.consumer(ConsumerLimits(state_bytes=10))
    consumer.feed(stream({"role": "assistant", "content": "answer"}).encode())
    consumer.feed(
        stream({"role": "meta", "type": "session.resume_hint", "session_id": "old"}).encode()
    )
    with pytest.raises(ConsumerFailure):
        consumer.feed(
            stream({"role": "meta", "type": "session.resume_hint", "session_id": "12345"}).encode()
        )
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.error.code == "stdout_limit_exceeded"
    assert failure.value.decoded is not None
    assert failure.value.decoded.output == "answer"
    assert failure.value.decoded.session_id == "old"
