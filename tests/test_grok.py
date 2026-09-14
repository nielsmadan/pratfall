import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import resolved as resolved_profile
from pratfall.adapters import grok
from pratfall.catalog import BY_NAME
from pratfall.cli import main
from pratfall.config import load_config, resolve_profile
from pratfall.errors import PratError
from pratfall.models import Options, ResolvedProfile, Usage


def result(**fields: object) -> dict[str, object]:
    return {
        "text": "answer",
        "stopReason": "end_turn",
        "sessionId": "session",
        "requestId": "request",
        **fields,
    }


def usage(**fields: object) -> dict[str, object]:
    return {
        "input_tokens": 3,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 7,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 17,
        **fields,
    }


def test_grok_literal_argv_prompt_and_native_controls() -> None:
    prompt = "-雪\n$(touch forbidden)\n".encode()
    invocation = grok.build(
        ResolvedProfile(
            BY_NAME["grok"],
            None,
            ("wrapper", "$HOME"),
            Options(
                model="grok-4.6",
                effort="high",
                max_turns=8,
                native_args=("--permission-mode=default",),
            ),
        ),
        prompt,
    )
    assert invocation.argv == (
        "wrapper",
        "$HOME",
        "--no-auto-update",
        "--output-format",
        "json",
        "--model",
        "grok-4.6",
        "--reasoning-effort",
        "high",
        "--max-turns",
        "8",
        "--permission-mode=default",
        "--single=-雪\n$(touch forbidden)\n",
    )
    assert invocation.stdin == b""


@pytest.mark.parametrize(
    "arguments",
    [
        ("--single=x",),
        ("-px",),
        ("--prompt-json=[]",),
        ("--prompt-file=x",),
        ("--output-format=plain",),
        ("--model=x",),
        ("-mx",),
        ("--effort=high",),
        ("--reasoning-effort=high",),
        ("--max-turns=2",),
        ("--cwd=x",),
        ("--continue",),
        ("--resume=x",),
        ("-rx",),
        ("--session-id=x",),
        ("--fork-session",),
        ("--worktree=x",),
        ("--no-auto-update",),
        ("--version",),
        ("--unknown",),
        ("agent",),
        ("@args",),
        ("--rules",),
        ("--yolo=true",),
    ],
)
def test_grok_rejects_contract_overrides(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PratError):
        grok.validate(resolved_profile("grok", Options(native_args=arguments)))


@pytest.mark.parametrize(
    "arguments",
    [
        ("--verbatim",),
        ("--no-plan",),
        ("--disable-web-search",),
        ("--rules=extra",),
        ("--allow", "Bash(git:*)"),
        ("--permission-mode=default",),
        ("--agent=reviewer",),
        ("--json-schema={}",),
        ("--no-wait-for-background",),
        ("--background-wait-timeout=30",),
    ],
)
def test_grok_accepts_verified_native_arguments(arguments: tuple[str, ...]) -> None:
    grok.validate(resolved_profile("grok", Options(native_args=arguments)))


@pytest.mark.parametrize(
    "options",
    [
        Options(fast=False),
        Options(max_budget_usd=1),
        Options(max_ai_credits=1),
    ],
)
def test_grok_unsupported_controls(native_contract_config: Path, options: Options) -> None:
    with pytest.raises(PratError, match="does not support"):
        resolve_profile(load_config(native_contract_config), "grok", options)


def test_grok_effort_union_is_validated(native_contract_config: Path) -> None:
    expected = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    assert BY_NAME["grok"].capabilities.effort_values == expected
    config = load_config(native_contract_config)
    for effort in expected:
        assert resolve_profile(config, "grok", Options(effort=effort)).options.effort == effort
    with pytest.raises(PratError, match="Grok Build accepts:"):
        resolve_profile(config, "grok", Options(effort="ultra"))


def test_grok_independent_native_contract(
    native_contract_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt = "-literal café 雪\n$HOME `id` $(touch forbidden)\n"
    status = main(
        [
            "--config",
            str(native_contract_config),
            "grok",
            "--json",
            "--model",
            "grok-4.7",
            "--effort",
            "high",
            "--max-turns",
            "8",
            f"--prompt={prompt}",
            "--",
            "--permission-mode=default",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    native = json.loads(payload["output"])
    assert status == payload["exit_code"] == payload["native_exit_code"] == 0
    assert native["argv"] == [
        "--no-auto-update",
        "--output-format",
        "json",
        "--model",
        "grok-4.7",
        "--reasoning-effort",
        "high",
        "--max-turns",
        "8",
        "--permission-mode=default",
        f"--single={prompt}",
    ]
    assert native["stdin"] == "" and native["prompt"] == prompt.strip()
    assert payload["reported_models"] == ["grok-4.6"]
    assert payload["usage"] == {
        "input_tokens": 3,
        "cached_input_tokens": 5,
        "cache_write_input_tokens": 7,
        "output_tokens": 2,
        "reasoning_output_tokens": 1,
    }
    assert payload["cost_usd"] == 0.0123456789


def test_grok_success_preserves_verified_accounting() -> None:
    decoded = grok.decode(
        json.dumps(
            result(
                usage=usage(),
                modelUsage={"grok-4.6": {"inputTokens": 3}},
                num_turns=2,
                total_cost_usd=0.0123456789,
                total_cost_usd_ticks=123_456_789,
            )
        )
    )
    assert decoded.output == "answer" and decoded.error is None
    assert decoded.usage == Usage(3, 5, 7, 2, 1)
    assert decoded.reported_models == ("grok-4.6",)
    assert decoded.cost_usd == 0.0123456789


@pytest.mark.parametrize(
    ("stop_reason", "output"),
    [
        ("max_tokens", "partial"),
        ("max_turn_requests", "partial"),
        ("refusal", "declined"),
        ("cancelled", ""),
    ],
)
def test_grok_incomplete_outcomes_retain_text(stop_reason: str, output: str) -> None:
    decoded = grok.decode(json.dumps(result(text=output, stopReason=stop_reason, usage=usage())))
    assert decoded.output == output and decoded.usage == Usage(3, 5, 7, 2, 1)
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert stop_reason in decoded.error.message


def test_grok_malformed_envelope_retains_verified_accounting() -> None:
    decoded = grok.decode(
        json.dumps(
            result(
                stopReason="future",
                usage=usage(),
                modelUsage={"grok-4.6": {"inputTokens": 3}},
                total_cost_usd=0.0123456789,
                total_cost_usd_ticks=123_456_789,
            )
        )
    )
    assert decoded.output == "answer"
    assert decoded.usage == Usage(3, 5, 7, 2, 1)
    assert decoded.reported_models == ("grok-4.6",)
    assert decoded.cost_usd == 0.0123456789
    assert decoded.error is not None and decoded.error.code == "protocol_error"


def test_grok_error_object_preserves_message_and_accounting() -> None:
    decoded = grok.decode(
        json.dumps(
            {
                "type": "error",
                "message": "authentication failed",
                "usage": usage(),
                "modelUsage": {"grok-4.6": {}},
            }
        )
    )
    assert decoded.output == "" and decoded.usage == Usage(3, 5, 7, 2, 1)
    assert decoded.reported_models == ("grok-4.6",)
    assert decoded.error is not None and decoded.error.code == "provider_error"
    assert decoded.error.message == "authentication failed"


@pytest.mark.parametrize(
    "fields",
    [
        {"text": None},
        {"stopReason": "future"},
        {"stopReason": []},
        {"sessionId": None},
        {"requestId": None},
        {"type": "result"},
        {"usage": []},
        {"usage": usage(input_tokens=True)},
        {"usage": usage(output_tokens=-1)},
        {"usage": usage(total_tokens=18)},
        {"modelUsage": []},
        {"num_turns": True},
        {"total_cost_usd": 1},
        {"total_cost_usd_ticks": 1},
        {"total_cost_usd": 1, "total_cost_usd_ticks": 1},
        {"cost_is_partial": True, "total_cost_usd": 1, "total_cost_usd_ticks": 10_000_000_000},
        {"usage": usage(), "usage_is_incomplete": True},
    ],
)
def test_grok_malformed_fields_keep_answer(fields: dict[str, object]) -> None:
    decoded = grok.decode(json.dumps(result(**fields)))
    if isinstance(fields.get("text"), str) or "text" not in fields:
        assert decoded.output == "answer"
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "[]",
        "{}",
        "garbage",
        '{"text":"a","text":"b"}',
        "[" * 2000 + "]" * 2000,
        '{"wide":' + "1" * 5000 + "}",
        '{"wide":0.' + "1" * 200 + "}",
        json.dumps(result()) + json.dumps(result()),
    ],
)
def test_grok_hostile_whole_json_is_normalized(payload: str) -> None:
    decoded = grok.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    ("payload", "output"),
    [
        (json.dumps(result(text="\ud800")), ""),
        ("\ud800", ""),
        (json.dumps(result(extra={"\ud800": "invalid key"})), "answer"),
    ],
)
def test_grok_invalid_unicode_is_normalized(payload: str, output: str) -> None:
    decoded = grok.decode(payload)
    assert decoded.output == output
    assert decoded.error is not None and decoded.error.code == "output_encoding"


@pytest.mark.parametrize("native_exit", [0, 17])
def test_grok_runner_preserves_structured_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    native_exit: int,
) -> None:
    payload = json.dumps({"type": "error", "message": "rate limited"})
    command = [sys.executable, "-c", f"import sys; print({payload!r}); sys.exit({native_exit})"]
    config = tmp_path / "failure.toml"
    config.write_text(f"version=1\n[agents.grok]\ncommand={json.dumps(command)}\n")
    status = main(["--config", str(config), "grok", "task", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert status == value["exit_code"] == (native_exit or 1)
    assert value["native_exit_code"] == native_exit and value["output"] == ""
    assert value["error"]["code"] == ("native_exit" if native_exit else "provider_error")
