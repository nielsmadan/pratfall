import json
import os
import stat
from pathlib import Path

import pytest

from pratfall.errors import PratError
from pratfall.local_trust import is_trusted, store_path, trust


def test_trust_records_the_real_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = trust(repo, os.environ)
    assert store == tmp_path / "state-home" / "pratfall" / "trusted.json"
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "version": 2,
        "directories": [os.path.realpath(repo)],
    }
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_trust_covers_the_directory_and_everything_under_it(tmp_path: Path) -> None:
    repo, sibling = tmp_path / "repo", tmp_path / "repo-other"
    (repo / "a" / "b").mkdir(parents=True)
    sibling.mkdir()
    assert is_trusted(repo, os.environ) is False
    trust(repo, os.environ)
    assert [is_trusted(path, os.environ) for path in (repo, repo / "a" / "b")] == [True, True]
    assert [is_trusted(path, os.environ) for path in (tmp_path, sibling)] == [False, False]


def test_trust_follows_symlinks_to_the_real_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)
    trust(link, os.environ)
    assert is_trusted(repo, os.environ) is True


def test_trust_keeps_other_entries_once_each(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    trust(second, os.environ)
    trust(first, os.environ)
    trust(second, os.environ)
    assert json.loads(store_path(os.environ).read_text(encoding="utf-8"))["directories"] == [
        os.path.realpath(first),
        os.path.realpath(second),
    ]


def test_per_file_store_from_version_one_is_dropped(tmp_path: Path) -> None:
    store = store_path(os.environ)
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "files": {str(tmp_path / ".pratfile"): "0"}}))
    assert is_trusted(tmp_path, os.environ) is False
    trust(tmp_path, os.environ)
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "version": 2,
        "directories": [os.path.realpath(tmp_path)],
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
    [
        b"not json",
        b"[]",
        b'{"version": 3, "directories": []}',
        b'{"version": 2, "directories": {}}',
        b'{"version": 2, "directories": [1]}',
        b'{"version": 2, "directories": ["relative"]}',
    ],
)
def test_invalid_store_fails_closed(contents: bytes) -> None:
    store = store_path(os.environ)
    store.parent.mkdir(parents=True)
    store.write_bytes(contents)
    with pytest.raises(PratError, match="invalid trust store"):
        is_trusted(Path("/repo"), os.environ)
