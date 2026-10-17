import json
import os
import stat
from pathlib import Path

import pytest

from pratfall.errors import PratError
from pratfall.local_trust import digest, store_path, trust, trust_state


def test_trust_records_path_and_content_digest(tmp_path: Path) -> None:
    config = tmp_path / ".pratfile"
    store = trust(config, b"version=1\n", os.environ)
    assert store == tmp_path / "state-home" / "pratfall" / "trusted.json"
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "version": 1,
        "files": {str(config): digest(b"version=1\n")},
    }
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_trust_state_distinguishes_untrusted_trusted_and_changed(tmp_path: Path) -> None:
    config = tmp_path / ".pratfile"
    assert trust_state(config, b"version=1\n", os.environ) == "untrusted"
    trust(config, b"version=1\n", os.environ)
    assert trust_state(config, b"version=1\n", os.environ) == "trusted"
    assert trust_state(config, b"version=1\n# edited\n", os.environ) == "changed"
    assert trust_state(tmp_path / "other" / ".pratfile", b"version=1\n", os.environ) == "untrusted"


def test_trust_keeps_other_entries_and_replaces_the_same_path(tmp_path: Path) -> None:
    first, second = tmp_path / "a" / ".pratfile", tmp_path / "b" / ".pratfile"
    trust(first, b"one", os.environ)
    trust(second, b"two", os.environ)
    trust(first, b"three", os.environ)
    assert json.loads(store_path(os.environ).read_text(encoding="utf-8"))["files"] == {
        str(first): digest(b"three"),
        str(second): digest(b"two"),
    }


@pytest.mark.parametrize(
    "state",
    [{}, {"XDG_STATE_HOME": ""}, {"XDG_STATE_HOME": "state"}, {"XDG_STATE_HOME": "./state"}],
)
def test_store_defaults_to_local_state_unless_xdg_is_absolute(
    tmp_path: Path, state: dict[str, str]
) -> None:
    env = {"HOME": str(tmp_path / "home"), **state}
    assert store_path(env) == tmp_path / "home" / ".local" / "state" / "pratfall" / "trusted.json"


@pytest.mark.parametrize(
    "contents",
    [b"not json", b"[]", b'{"version": 2, "files": {}}', b'{"version": 1, "files": {"a": 1}}'],
)
def test_invalid_store_fails_closed(contents: bytes) -> None:
    store = store_path(os.environ)
    store.parent.mkdir(parents=True)
    store.write_bytes(contents)
    with pytest.raises(PratError, match="invalid trust store"):
        trust_state(Path("/repo/.pratfile"), b"", os.environ)
