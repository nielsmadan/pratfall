import json
import sys
from pathlib import Path

import pytest

from adapter_helpers import resolved
from pratfall.adapters import goose
from pratfall.cli import main
from pratfall.errors import PratError
from pratfall.models import Options


def result(**metadata: object) -> dict[str, object]:
    return {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "prompt"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "earlier"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "final"},
                    {"type": "thinking", "thinking": "private"},
                ],
            },
            {"role": "assistant", "content": [{"type": "toolRequest", "id": "tool"}]},
        ],
        "metadata": {"status": "completed", "cost_usd": 0.25, **metadata},
    }


def test_goose_selects_last_assistant_text_and_native_cost() -> None:
    decoded = goose.decode(json.dumps(result()))
    assert decoded.output == "final"
    assert decoded.cost_usd == 0.25
    assert decoded.error is None
    assert decoded.usage is None


@pytest.mark.parametrize(
    ("metadata", "code"),
    [
        ({"status": "error"}, "provider_error"),
        ({"status": "future"}, "protocol_error"),
        ({"cost_usd": -1}, "protocol_error"),
    ],
)
def test_goose_failure_retains_answer(metadata: dict[str, object], code: str) -> None:
    decoded = goose.decode(json.dumps(result(**metadata)))
    assert decoded.output == "final"
    assert decoded.error is not None and decoded.error.code == code


@pytest.mark.parametrize("payload", ["{}", "[]", '{"messages":[],"metadata":{}}'])
def test_goose_requires_completion_envelope(payload: str) -> None:
    decoded = goose.decode(payload)
    assert decoded.error is not None and decoded.error.code == "protocol_error"


@pytest.mark.parametrize(
    "flag",
    [
        "--resume",
        "--interactive",
        "--text=other",
        "--recipe=other",
        "--model=other",
        "--output-format=text",
    ],
)
def test_goose_rejects_contract_overrides(flag: str) -> None:
    with pytest.raises(PratError):
        goose.validate(resolved("goose", Options(native_args=(flag,))))


def test_goose_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = "-雪\n$HOME $(touch forbidden)\n"
    assert (
        main(
            [
                "--config",
                str(native_contract_config),
                "goose",
                f"--prompt={prompt}",
                "--model=m",
                "--max-turns=3",
                "--json",
                "--",
                "--provider=p",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    record = json.loads(data["output"])
    assert record["argv"] == [
        "run",
        "--quiet",
        "--no-session",
        "--output-format",
        "json",
        "--instructions",
        "-",
        "--model",
        "m",
        "--max-turns",
        "3",
        "--provider=p",
    ]
    assert record["stdin"] == prompt
    assert data["status"] == "success"


@pytest.mark.parametrize("json_mode", [False, True])
def test_goose_startup_error_reaches_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    diagnostic = (
        "error: Error Configuration value not found: OPENROUTER_API_KEY.\n"
        "Please check your system keychain and run 'goose configure' again."
    )
    script = tmp_path / "goose.py"
    script.write_text(f"print({diagnostic!r})\nraise SystemExit(1)\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        "version=1\n[agents.goose]\ncommand=" + json.dumps([sys.executable, str(script)]) + "\n",
        encoding="utf-8",
    )
    args = ["--config", str(config), "goose", "test"]
    if json_mode:
        args.append("--json")
    assert main(args) == 1
    captured = capsys.readouterr()
    if json_mode:
        payload = json.loads(captured.out)
        assert payload["error"] == {
            "code": "native_exit",
            "message": "Goose exited with status 1.\n" + diagnostic,
        }
        assert payload["native_exit_code"] == 1
        assert payload["output"] == ""
    else:
        assert diagnostic in captured.err
