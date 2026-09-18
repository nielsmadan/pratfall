import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import resolved
from pratfall.adapters import pi
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.consumer import ConsumerFailure, ConsumerLimits
from pratfall.errors import PratError
from pratfall.models import Options, Usage


def stream(*events: object) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def message(text: str = "final", /, **fields: object) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stopReason": "stop",
        "model": "pi-model",
        "usage": {
            "input": 3,
            "output": 2,
            "cacheRead": 1,
            "cacheWrite": 4,
            "cost": {"total": 0.01},
        },
        **fields,
    }


def end(value: object) -> dict[str, object]:
    return {"type": "message_end", "message": value}


SETTLED = {"type": "agent_settled"}


def test_pi_build_preserves_literal_stdin_and_separate_values() -> None:
    options = Options(
        model="provider/model",
        effort="high",
        tools=("read", "bash"),
        disabled_tools=("write",),
        attachments=("/example/a b.png",),
        native_args=("--provider=test", "--system-prompt", "--tools=literal", "--no-session"),
    )
    invocation = pi.build(resolved("pi", options), b"@literal\n$(id)\n")
    assert invocation.argv == (
        "pi-wrapper",
        "native",
        "--print",
        "--mode",
        "json",
        "--model",
        "provider/model",
        "--thinking",
        "high",
        "--tools",
        "read,bash",
        "--exclude-tools",
        "write",
        "--provider",
        "test",
        "--system-prompt",
        "--tools=literal",
        "--no-session",
        "@/example/a b.png",
    )
    assert invocation.stdin == b"@literal\n$(id)\n"
    assert pi.build(resolved("pi", Options(tools=())), b"x").argv[-2:] == ("--tools", "")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--mode=rpc",),
        ("-p",),
        ("--resume",),
        ("--model=x",),
        ("--thinking=high",),
        ("@secret",),
        ("--extension", "x"),
        ("--unknown",),
        ("auth",),
        ("--provider",),
    ],
)
def test_pi_rejects_unsafe_native_modes(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        pi.validate(resolved("pi", Options(native_args=arguments)))


@pytest.mark.parametrize(
    "options",
    [
        Options(tools=("read,write",)),
        Options(tools=("\ufeffread",)),
        Options(disabled_tools=("read ",)),
        Options(tools=(), native_args=("--no-tools",)),
        Options(disabled_tools=("write",), native_args=("-xt", "read")),
        Options(attachments=("/example/a\u00a0b.png",)),
    ],
)
def test_pi_rejects_public_control_ambiguity(options: Options) -> None:
    with pytest.raises(PratError):
        pi.validate(resolved("pi", options))


def test_pi_native_passthrough_and_empty_deny_are_valid() -> None:
    pi.validate(
        resolved(
            "pi",
            Options(
                disabled_tools=(),
                native_args=("--exclude-tools=write", "--provider", "test"),
            ),
        )
    )


@pytest.mark.parametrize(
    "options",
    [
        Options(instructions="literal"),
        Options(add_dirs=("/example",)),
        Options(fast=False),
        Options(native_agent="research"),
        Options(schema="schema.json"),
        Options(max_turns=2),
    ],
)
def test_pi_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "pi", options)


def test_pi_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    attachment = tmp_path / "image.png"
    attachment.write_bytes(b"fake image")
    prompt = "  @literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            "pi",
            "--json",
            f"--prompt={prompt}",
            "--model",
            "provider/model",
            "--effort",
            "high",
            "--tools",
            "read",
            "--disable-tools",
            "write",
            "--attach",
            str(attachment),
            "--",
            "--provider=test",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    native = json.loads(payload["output"])
    assert status == payload["exit_code"] == payload["native_exit_code"] == 0
    assert native["stdin"] == prompt
    assert native["prompt"] == prompt.strip()
    assert native["argv"] == [
        "--print",
        "--mode",
        "json",
        "--model",
        "provider/model",
        "--thinking",
        "high",
        "--tools",
        "read",
        "--exclude-tools",
        "write",
        "--provider",
        "test",
        f"@{attachment.resolve()}",
    ]
    assert payload["reported_models"] == ["pi-model"]
    assert payload["cost_usd"] == 0.01
    assert payload["usage"]["input_tokens"] == 3


def test_pi_final_text_and_accounting_ignore_repeated_snapshots_and_tools() -> None:
    first = message("interim", stopReason="toolUse")
    last = message("café 雪", model="second")
    payload = stream(
        {"type": "session", "version": 3},
        {"type": "agent_start"},
        end({"role": "user", "content": "question"}),
        end(first),
        {"type": "turn_end", "message": first},
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "noise"},
        },
        {"type": "tool_execution_end", "isError": True},
        end({"role": "toolResult", "content": [{"type": "text", "text": "tool"}]}),
        end(last),
        {"type": "agent_end", "messages": [first, last]},
        SETTLED,
    )
    consumer = pi.consumer()
    for byte in payload.rstrip("\n").encode():
        consumer.feed(bytes([byte]))
    decoded = consumer.finish()
    assert decoded.error is None and decoded.output == "café 雪"
    assert decoded.usage == Usage(6, 2, 8, 4)
    assert decoded.reported_models == ("pi-model", "second")
    assert decoded.cost_usd == 0.02


def test_pi_retry_recovers_provider_error() -> None:
    decoded = pi.decode(
        stream(
            end(message("partial", stopReason="error", errorMessage="rate limit")),
            {"type": "agent_end", "willRetry": True},
            {"type": "auto_retry_start"},
            {"type": "agent_start"},
            {"type": "auto_retry_end", "success": True},
            end(message("recovered")),
            {"type": "agent_end"},
            SETTLED,
        )
    )
    assert decoded.output == "recovered" and decoded.error is None
    assert decoded.usage == Usage(6, 2, 8, 4)


@pytest.mark.parametrize("reason", ["error", "aborted"])
def test_pi_provider_failure_retains_partial_text(reason: str) -> None:
    decoded = pi.decode(
        stream(
            end(message("partial")),
            end(message("", content=[], stopReason=reason, errorMessage="failed")),
            SETTLED,
        )
    )
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        stream(SETTLED),
        stream(end(message())),
        stream(end(message()), {"type": "agent_end"}),
        stream(end(message()), SETTLED, SETTLED),
        stream(end(message()), {"type": "unknown"}, SETTLED),
    ],
)
def test_pi_requires_settled_answer(payload: str) -> None:
    decoded = pi.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "fields",
    [
        {"content": None},
        {"content": [{"type": "text", "text": 1}]},
        {"content": [{"type": "future"}]},
        {"stopReason": "pending"},
        {"usage": []},
        {"usage": {"input": True}},
        {"model": []},
        {
            "usage": {
                "input": 1,
                "output": 2,
                "cacheRead": 0,
                "cacheWrite": 0,
                "cost": {"total": -1},
            }
        },
    ],
)
def test_pi_malformed_message_is_protocol_failure(fields: dict[str, object]) -> None:
    decoded = pi.decode(stream(end(message("partial")), end(message(**fields)), SETTLED))
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_pi_empty_success_clears_prior_text_and_ignores_thinking() -> None:
    decoded = pi.decode(
        stream(
            end(message("prior")),
            end(
                message(
                    content=[
                        {"type": "thinking", "thinking": "private"},
                        {"type": "toolCall", "name": "read"},
                    ]
                )
            ),
            SETTLED,
        )
    )
    assert decoded.error is None and decoded.output == ""


def test_pi_missing_accounting_keeps_totals_unknown() -> None:
    decoded = pi.decode(stream(end(message(usage=None)), end(message()), SETTLED))
    assert decoded.error is None and decoded.usage is None and decoded.cost_usd is None


def test_pi_bounds_replacements_and_models() -> None:
    consumer = pi.consumer(ConsumerLimits(state_bytes=128, records=3))
    for _ in range(100):
        consumer.feed(stream(end(message("x"))).encode())
    consumer.feed(stream(SETTLED).encode())
    assert consumer.finish().output == "x"
    consumer = pi.consumer(ConsumerLimits(state_bytes=64))
    consumer.feed(stream(end(message("partial"))).encode())
    with pytest.raises(ConsumerFailure):
        consumer.feed(stream(end(message("x" * 100))).encode())
    with pytest.raises(ConsumerFailure) as failure:
        consumer.finish()
    assert failure.value.decoded is not None and failure.value.decoded.output == "partial"


def test_pi_invalid_unicode_preserves_prior_answer() -> None:
    decoded = pi.decode(stream(end(message("partial")), end(message("\ud800")), SETTLED))
    assert decoded.output == "partial"
    assert decoded.error is not None and decoded.error.code == "output_encoding"


@pytest.mark.parametrize("native_exit", [0, 17])
def test_pi_zero_exit_provider_errors_are_normalized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    payload = stream(end(message("partial", stopReason="error", errorMessage="failed")), SETTLED)
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "pi.toml"
    config.write_text(f"version=1\n[agents.pi]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "pi", "task", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == (native_exit or 1)
    assert result["output"] == "partial"
    assert result["error"]["code"] == ("native_exit" if native_exit else "provider_error")
