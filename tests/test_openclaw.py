import json

import pytest

from adapter_helpers import resolved
from pratfall.adapters import openclaw
from pratfall.errors import PratError
from pratfall.models import Options, ResultError, Usage


def openclaw_result(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ok": True,
        "status": "ok",
        "final": "final answer",
        "payloads": [{"text": "final answer"}],
        "usage": {"input": 12, "output": 4, "total": 16},
    }
    value.update(changes)
    return value


def test_openclaw_builds_embedded_stdin_invocation() -> None:
    invocation = openclaw.build(
        resolved(
            "openclaw",
            Options(
                model="provider/model",
                effort="adaptive",
                timeout=42.5,
                native_args=("--isolated", "--code-mode", "code"),
            ),
        ),
        b"multi\nline",
    )
    assert invocation.argv == (
        "openclaw-wrapper",
        "native",
        "agent",
        "exec",
        "--json",
        "--message-file",
        "-",
        "--model",
        "provider/model",
        "--thinking",
        "adaptive",
        "--timeout",
        "43",
        "--isolated",
        "--code-mode",
        "code",
    )
    assert invocation.stdin == b"multi\nline"


@pytest.mark.parametrize(("timeout", "native"), [(0.25, "1"), (1e6, "1000000")])
def test_openclaw_serializes_native_timeout_as_ceiling_plain_integer(
    timeout: float, native: str
) -> None:
    invocation = openclaw.build(resolved("openclaw", Options(timeout=timeout)), b"prompt")
    assert invocation.argv[-2:] == ("--timeout", native)


def test_openclaw_explicit_fallback_requires_model() -> None:
    with pytest.raises(PratError, match="fallback requires an explicit model"):
        openclaw.validate(
            resolved("openclaw", Options(native_args=("--fallback", "provider/backup")))
        )


def test_openclaw_accepts_repeatable_fallback_with_model() -> None:
    arguments = ("--fallback", "provider/backup", "--fallback=provider/last")
    invocation = openclaw.build(
        resolved("openclaw", Options(model="provider/primary", native_args=arguments)),
        b"prompt",
    )
    assert invocation.argv[-3:] == arguments


def test_openclaw_decodes_stable_success_envelope() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                costUsd=0.0021,
                model="model",
                provider="provider",
                sessionId="session",
            )
        )
    )
    assert decoded.output == "final answer"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.reported_models == ("provider/model",)
    assert decoded.cost_usd == 0.0021
    assert decoded.error is None


def test_openclaw_preserves_partial_final_on_provider_failure() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                final="safe partial",
                payloads=[{"text": "safe partial"}],
                error={"message": "provider unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe partial"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("provider_error", "provider unavailable")


def test_openclaw_explicit_error_overrides_contradictory_success_and_malformed_usage() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                usage={"input": 1},
                error={"message": "provider unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage is None
    assert decoded.error == ResultError("provider_error", "provider unavailable")


def test_openclaw_error_payload_overrides_contradictory_success() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                payloads=[
                    {"text": "safe final", "future": {"additive": True}},
                    {"text": "model failed", "isError": True},
                ],
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("provider_error", "model failed")


def test_openclaw_preserves_final_when_success_error_field_is_malformed() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                final="safe final",
                error={"message": 3, "kind": "model_error"},
            )
        )
    )
    assert decoded.output == "safe final"
    assert decoded.usage == Usage(input_tokens=12, output_tokens=4)
    assert decoded.error == ResultError("protocol_error", "OpenClaw error is malformed.")


def test_openclaw_provider_timeout_is_explicit() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="timeout",
                final="partial",
                error={"message": "deadline exceeded", "kind": "timeout"},
            )
        )
    )
    assert decoded.output == "partial"
    assert decoded.timed_out is True
    assert decoded.error == ResultError("timeout", "deadline exceeded")


def test_openclaw_omitted_usage_stays_unknown() -> None:
    value = openclaw_result()
    del value["usage"]
    decoded = openclaw.decode(json.dumps(value))
    assert decoded.usage is None
    assert decoded.error is None


def test_openclaw_model_without_provider_and_zero_cost_are_reported() -> None:
    decoded = openclaw.decode(json.dumps(openclaw_result(model="model", costUsd=0)))
    assert decoded.reported_models == ("model",)
    assert decoded.cost_usd == 0
    assert decoded.error is None


@pytest.mark.parametrize("provider", ["", 3])
def test_openclaw_malformed_provider_drops_model_but_preserves_cost(provider: object) -> None:
    decoded = openclaw.decode(
        json.dumps(openclaw_result(model="model", provider=provider, costUsd=0.25))
    )
    assert decoded.reported_models is None
    assert decoded.cost_usd == 0.25
    assert decoded.error == ResultError("protocol_error", "OpenClaw provider is malformed.")


@pytest.mark.parametrize("model_value", [None, "missing"])
@pytest.mark.parametrize("provider", ["", 3])
def test_openclaw_validates_provider_without_a_model(model_value: object, provider: object) -> None:
    value = openclaw_result(model=model_value, provider=provider, costUsd=0.25)
    if model_value == "missing":
        del value["model"]
    decoded = openclaw.decode(json.dumps(value))
    assert decoded.reported_models is None
    assert decoded.cost_usd == 0.25
    assert decoded.error == ResultError("protocol_error", "OpenClaw provider is malformed.")


@pytest.mark.parametrize("model_value", [None, "missing"])
@pytest.mark.parametrize("provider_value", [None, "missing"])
def test_openclaw_missing_accounting_identity_remains_unknown(
    model_value: object, provider_value: object
) -> None:
    value = openclaw_result(model=model_value, provider=provider_value)
    if model_value == "missing":
        del value["model"]
    if provider_value == "missing":
        del value["provider"]
    decoded = openclaw.decode(json.dumps(value))
    assert decoded.reported_models is None
    assert decoded.error is None


def test_openclaw_provider_failure_preserves_accounting_and_outranks_malformed_cost() -> None:
    decoded = openclaw.decode(
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                model="model",
                provider="provider",
                costUsd=True,
                error={"message": "unavailable", "kind": "model_error"},
            )
        )
    )
    assert decoded.reported_models == ("provider/model",)
    assert decoded.cost_usd is None
    assert decoded.error == ResultError("provider_error", "unavailable")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        json.dumps({"future": True}),
        json.dumps(openclaw_result(status="future")),
        json.dumps(openclaw_result(status=[])),
        json.dumps(openclaw_result(status={})),
        json.dumps(openclaw_result(ok=False)),
        json.dumps(openclaw_result(final=None)),
        json.dumps(openclaw_result(payloads=None)),
        json.dumps(openclaw_result(payloads=[3])),
        json.dumps(openclaw_result(payloads=[{"text": 3}])),
        json.dumps(openclaw_result(payloads=[{"mediaUrl": 3}])),
        json.dumps(openclaw_result(payloads=[{"mediaUrls": ["image.png", 3]}])),
        json.dumps(openclaw_result(payloads=[{"isError": "yes"}])),
        json.dumps(openclaw_result(usage={"input": 1, "output": 2})),
        json.dumps(
            openclaw_result(
                ok=False,
                status="error",
                error={"message": 3, "kind": "model_error"},
            )
        ),
        json.dumps(openclaw_result()) + "\n" + json.dumps(openclaw_result()),
    ],
)
def test_openclaw_rejects_malformed_or_truncated_results(payload: str) -> None:
    decoded = openclaw.decode(payload)
    assert decoded.error is not None
    assert decoded.error.code == "protocol_error"
