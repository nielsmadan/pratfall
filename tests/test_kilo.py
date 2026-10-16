import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from adapter_helpers import opencode_usage, resolved
from pratfall.adapters import kilo
from pratfall.cli import main
from pratfall.errors import PratError
from pratfall.models import Options, Usage
from pratfall.output import normalize
from pratfall.runner import raw_stdout, run


def test_kilo_disables_daemon_without_rewriting_command_prefix() -> None:
    prompt = b"-literal $HOME $(touch forbidden)\n"
    invocation = kilo.build(
        resolved("kilo", Options(model="provider/model", effort="high")), prompt
    )
    assert invocation.argv == (
        "kilo-wrapper",
        "native",
        "run",
        "--format",
        "json",
        "--model",
        "provider/model",
        "--variant",
        "high",
    )
    assert invocation.stdin == prompt
    assert invocation.env == (("KILO_NO_DAEMON", "1"),)


@pytest.mark.parametrize(
    "flag",
    [
        "--attach=http://localhost",
        "--session=other",
        "--continue",
        "--format=text",
        "--variant=other",
        "--dir=other",
    ],
)
def test_kilo_rejects_managed_or_remote_flags(flag: str) -> None:
    with pytest.raises(PratError):
        kilo.validate(resolved("kilo", Options(native_args=(flag,))))


def test_kilo_shared_stream_contract_retains_native_usage() -> None:
    events = [
        {
            "type": "text",
            "sessionID": "s",
            "part": {"id": "text", "type": "text", "text": "answer", "time": {"end": 1}},
        },
        {
            "type": "step_finish",
            "part": {
                "id": "step",
                "type": "step-finish",
                "reason": "stop",
                "cost": 0.5,
                "tokens": opencode_usage(),
            },
        },
    ]
    decoded = kilo.decode("".join(json.dumps(event) + "\n" for event in events))
    assert decoded.output == "answer"
    assert decoded.error is None
    assert decoded.cost_usd == 0.5
    assert decoded.usage == Usage(10, 3, 1, 4, 2)


def test_kilo_independent_native_contract(
    native_contract_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = "-literal 雪\n"
    assert (
        main(["--config", str(native_contract_config), "kilo", f"--prompt={prompt}", "--json"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    record = json.loads(result["output"])
    assert record["argv"] == ["run", "--format", "json"]
    assert record["stdin"] == prompt
    assert result["status"] == "success"


@pytest.mark.parametrize("exists", [False, True])
def test_kilo_preserves_executable_startup_failures(tmp_path: Path, exists: bool) -> None:
    executable = tmp_path / "kilo"
    if exists:
        executable.write_text("not executable", encoding="utf-8")
        executable.chmod(0o600)
    profile = replace(resolved("kilo"), command=(str(executable),))
    process = run(kilo.build(profile, b"prompt"), tmp_path, 5)
    result = normalize(profile, process, kilo.decode(""))
    assert result.error is not None
    assert result.error.code == ("executable_not_executable" if exists else "executable_not_found")
    assert result.native_exit_code is None


def test_kilo_environment_override_is_child_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KILO_NO_DAEMON", "0")
    monkeypatch.setenv("PRAT_TEST_INHERITED", "kept")
    script = tmp_path / "native.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps([os.environ['KILO_NO_DAEMON'], os.environ['PRAT_TEST_INHERITED']]))\n",
        encoding="utf-8",
    )
    profile = replace(resolved("kilo"), command=(sys.executable, str(script)))
    process = run(kilo.build(profile, b"prompt"), tmp_path, 5)
    assert process.native_exit_code == 0
    assert json.loads(raw_stdout(process.capture)) == ["1", "kept"]
    assert os.environ["KILO_NO_DAEMON"] == "0"


@pytest.mark.parametrize("json_mode", [False, True])
def test_kilo_preview_includes_child_environment(
    capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    args = ["kilo", "task", "--dry-run"] + (["--json"] if json_mode else [])
    assert main(args) == 0
    output = capsys.readouterr().out
    if json_mode:
        payload = json.loads(output)
        assert payload["argv"] == ["kilo", "run", "--format", "json"]
        assert payload["env"] == {"KILO_NO_DAEMON": "1"}
    else:
        assert "command: env KILO_NO_DAEMON=1 kilo run --format json" in output
