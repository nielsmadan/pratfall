import os
from pathlib import Path

import pytest

from pratfall import option_paths
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


def test_attachment_validation_reads_one_byte_and_returns_native_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.binary"
    with path.open("wb") as stream:
        stream.seek(16 * 1024 * 1024)
        stream.write(b"\xff")
    sizes: list[int] = []
    read = os.read

    def tracked_read(descriptor: int, size: int) -> bytes:
        sizes.append(size)
        return read(descriptor, size)

    monkeypatch.setattr(os, "read", tracked_read)
    assert option_paths.prepare_attachments((str(path),), OptionOrigin("attachments")) == (
        str(path),
    )
    assert sizes == [1]


def test_attachment_descriptor_is_closed_after_failed_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"\xff")
    descriptors: list[int] = []

    def failed_read(descriptor: int, _size: int) -> bytes:
        descriptors.append(descriptor)
        raise OSError("read failed")

    monkeypatch.setattr(os, "read", failed_read)
    with pytest.raises(
        PratError, match=r"profile\.attachments: cannot read attachment.*read failed"
    ):
        option_paths.prepare_attachments((str(path),), OptionOrigin("profile.attachments"))
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_attachment_changed_to_nonregular_file_is_rejected_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"\xff")
    descriptors: list[int] = []
    directory_stat = tmp_path.stat()
    fstat = os.fstat

    def changed_stat(descriptor: int) -> os.stat_result:
        descriptors.append(descriptor)
        return directory_stat

    monkeypatch.setattr(os, "fstat", changed_stat)
    with pytest.raises(PratError, match="not a regular attachment file"):
        option_paths.prepare_attachments((str(path),), OptionOrigin("attachments"))
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        fstat(descriptors[0])
