from pathlib import Path

import pytest

from pratfall.errors import PratError
from pratfall.models import OptionOrigin
from pratfall.option_paths import prepare_directories, resolve_path


def test_path_resolution_expands_home_and_preserves_parent_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    origin = OptionOrigin("paths")
    assert resolve_path("~/link/../extra", Path("/other"), origin) == str(
        tmp_path / "link" / ".." / "extra"
    )


def test_unknown_user_path_reports_its_origin(tmp_path: Path) -> None:
    with pytest.raises(PratError, match=r"profile\.add_dirs: cannot expand") as caught:
        resolve_path(
            "~prat-nonexistent-test-user/extra", tmp_path, OptionOrigin("profile.add_dirs")
        )
    assert caught.value.code == "invalid_config"


def test_directory_inspection_errors_keep_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_path: Path) -> bool:
        raise OSError("unavailable")

    monkeypatch.setattr(Path, "is_dir", fail)
    with pytest.raises(PratError, match="command line: cannot inspect directory") as caught:
        prepare_directories((str(tmp_path),), OptionOrigin("command line", "invalid_arguments"))
    assert caught.value.code == "invalid_arguments"
