import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))


@pytest.fixture
def native_contract_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = Path(__file__).resolve().parents[1] / "scripts" / "qa_installed.py"
    config = tmp_path / "native-contract.toml"
    lines = ["version=1"]
    for agent in (
        "openhands",
        "warp",
        "iflow",
        "qwen",
        "amp",
        "reasonix",
        "droid",
        "kimi",
        "vibe",
        "crush",
        "devin",
        "cortex",
    ):
        command = [sys.executable, str(script), "--fake-native", agent]
        lines.extend((f"[agents.{agent}]", f"command={json.dumps(command)}"))
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("PRAT_QA_LOG", str(tmp_path / "native-calls.jsonl"))
    return config
