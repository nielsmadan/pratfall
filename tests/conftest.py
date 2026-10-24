import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pratfall import local_trust


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", os.defpath)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "/nonexistent/prat-test-editor")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.setattr("pratfall.config.trust_state", lambda _path, _contents, _env: "trusted")
    monkeypatch.chdir(tmp_path)
    stdin = io.StringIO("")
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdin", stdin)


@pytest.fixture
def no_process_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("validation must not launch a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture(scope="session")
def opencode_one_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("opencode-bin")
    executable = directory / "opencode"
    executable.write_text("#!/bin/sh\necho 1.19.3\n")
    executable.chmod(0o755)
    return directory


@pytest.fixture
def opencode_one_on_path(opencode_one_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"{opencode_one_bin}{os.pathsep}{os.defpath}")


@pytest.fixture
def real_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pratfall.config.trust_state", local_trust.trust_state)


@pytest.fixture
def native_contract_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = Path(__file__).resolve().parents[1] / "scripts" / "qa_installed.py"
    config = tmp_path / "native-contract.toml"
    lines = ["version=1"]
    for agent in (
        "kimi",
        "kilo",
        "goose",
        "warp",
        "qwen",
        "amp",
        "reasonix",
        "droid",
        "vibe",
        "crush",
        "devin",
        "cortex",
        "grok",
        "pi",
    ):
        command = [sys.executable, str(script), "--fake-native", agent]
        lines.extend((f"[agents.{agent}]", f"command={json.dumps(command)}"))
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("PRAT_QA_LOG", str(tmp_path / "native-calls.jsonl"))
    return config
