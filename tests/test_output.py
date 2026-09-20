import signal

import pytest

from pratfall.catalog import BY_NAME
from pratfall.models import (
    DecodedOutput,
    Options,
    RawCapture,
    ResolvedProfile,
    ResultError,
    Usage,
)
from pratfall.output import extract_code, normalize, result_dict
from pratfall.runner import ProcessResult

RESOLVED = ResolvedProfile(
    BY_NAME["codex"], "profile", ("codex",), Options(model="model", timeout=10)
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("before\n```python\nprint('hello')\n```\nafter", "print('hello')\n"),
        ("~~~text\n  indented\n\talso indented\n~~~", "  indented\n\talso indented\n"),
        (" ```\none\n ```", "one\n"),
        ("  ```\ntwo\n  ```", "two\n"),
        ("   ```\nthree\n   ```", "three\n"),
        ("```\nbody\n````` \t\n", "body\n"),
        ("~~~~`info`\nbody\n~~~~~\t", "body\n"),
        ("```\n```", ""),
        ("```\n\n```", "\n"),
        ("```\nfirst\n```\n~~~\nsecond\n~~~", "first\n"),
        ("```\r\n  π 🐍\r\n\r\n```\r\n", "  π 🐍\r\n\r\n"),
        ("```\rbody\r```", "body\r"),
        ("```\nbody\r\nmore\r```", "body\r\nmore\r"),
        ("```\nbody\u2028```\u2029\n```", "body\u2028```\u2029\n"),
        ("```bad`info\n~~~\nvalid\n~~~", "valid\n"),
        (
            "````\n```\n~~~\n```` closing text\n    ````\n\t````\n````\u00a0\n````",
            "```\n~~~\n```` closing text\n    ````\n\t````\n````\u00a0\n",
        ),
    ],
)
def test_extract_code_preserves_first_fenced_body(answer: str, expected: str) -> None:
    assert extract_code(answer) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "ordinary answer\n",
        "```",
        "```python\nunclosed",
        "````\nfirst unclosed\n~~~\nother complete block\n~~~\n",
        "    ```\nnot a fence\n    ```",
        "\t~~~\nnot a fence\n\t~~~",
        "``\nshort\n``",
        "~~\nshort\n~~",
        "prefix ```\ninline\nend ```",
        "```bad`info\ninvalid\n```bad`info",
        "```\nwrong character\n~~~",
        "````\nshort closing\n```",
        "```\nclosing info\n``` python",
        "before\u2028```\nnot an opening\nend",
    ],
)
def test_extract_code_returns_original_without_complete_first_block(answer: str) -> None:
    assert extract_code(answer) == answer


def process(
    native: int | None = 0,
    error: ResultError | None = None,
    *,
    timeout: bool = False,
    interrupted: int | None = None,
) -> ProcessResult:
    return ProcessResult(RawCapture(), b"", native, 42, error, timeout, interrupted)


def test_runner_failure_outranks_native_and_decoder_failures() -> None:
    runner_error = ResultError("stdout_limit_exceeded", "too much")
    decoded = DecodedOutput(
        output="safe partial",
        usage=Usage(input_tokens=1),
        reported_models=("native-a", "native-b"),
        cost_usd=0,
        error=ResultError("provider_error", "provider"),
    )
    result = normalize(RESOLVED, process(9, runner_error), decoded)
    assert result.status == "error"
    assert result.exit_code == 1
    assert result.native_exit_code == 9
    assert result.output == "safe partial"
    assert result.reported_models == ("native-a", "native-b")
    assert result.cost_usd == 0
    assert result.error == runner_error


def test_native_nonzero_outranks_protocol_failure() -> None:
    result = normalize(
        RESOLVED,
        process(23),
        DecodedOutput(error=ResultError("protocol_error", "truncated")),
    )
    assert result.exit_code == 23
    assert result.error is not None
    assert result.error.code == "native_exit"


def test_provider_timeout_maps_to_124_even_with_native_nonzero() -> None:
    error = ResultError("timeout", "provider timeout")
    result = normalize(RESOLVED, process(2), DecodedOutput(error=error, timed_out=True))
    assert result.status == "timeout"
    assert result.exit_code == 124
    assert result.native_exit_code == 2


def test_process_io_failure_outranks_decoded_provider_timeout() -> None:
    io_error = ResultError("stderr_limit_exceeded", "too much stderr")
    provider_timeout = ResultError("timeout", "provider timeout")
    result = normalize(
        RESOLVED,
        process(2, io_error),
        DecodedOutput(error=provider_timeout, timed_out=True),
    )
    assert result.status == "error"
    assert result.exit_code == 1
    assert result.error == io_error


def test_cancellation_outranks_timeout() -> None:
    error = ResultError("interrupted", "signal")
    result = normalize(
        RESOLVED,
        process(-signal.SIGKILL, error, timeout=True, interrupted=signal.SIGTERM),
        DecodedOutput(timed_out=True),
    )
    assert result.status == "interrupted"
    assert result.exit_code == 128 + signal.SIGTERM


def test_native_signal_and_success_json_shape() -> None:
    interrupted = normalize(RESOLVED, process(-signal.SIGKILL), DecodedOutput())
    assert interrupted.status == "interrupted"
    assert interrupted.exit_code == 128 + signal.SIGKILL
    success = normalize(
        RESOLVED,
        process(),
        DecodedOutput(output="answer", usage=Usage(output_tokens=2)),
    )
    assert result_dict(success) == {
        "schema_version": 1,
        "agent": "codex",
        "profile": "profile",
        "model": "model",
        "reported_models": None,
        "cost_usd": None,
        "structured_output": None,
        "status": "success",
        "output": "answer",
        "exit_code": 0,
        "native_exit_code": 0,
        "native_session_id": None,
        "duration_ms": 42,
        "usage": {
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": 2,
            "reasoning_output_tokens": None,
        },
        "error": None,
    }


def test_envelope_reports_the_session_the_agent_named() -> None:
    result = normalize(RESOLVED, process(), DecodedOutput(output="answer", session_id="abc123"))
    assert result_dict(result)["native_session_id"] == "abc123"
