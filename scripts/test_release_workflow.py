import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).with_name("release_workflow.py")
SPEC = importlib.util.spec_from_file_location("release_workflow", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_workflow)


def result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize(
    ("current", "tags", "expected"),
    [
        ("v0.1.0", [], None),
        ("v1.2.3", ["latest", "v1.2", "v1.2.3-rc1", "v01.2.2"], None),
        ("v1.2.3", ["v2.0.0", "v1.2.4", "v1.2.2", "v1.0.9"], "v1.2.2"),
        ("v1.2.0", ["v1.3.0", "v1.2.0", "v1.1.9", "v1.1.10"], "v1.1.10"),
    ],
)
def test_previous_tag_selects_strict_semver_below_current(
    current: str, tags: list[str], expected: str | None
) -> None:
    assert release_workflow.previous_tag(current, tags) == expected


def test_previous_tag_rejects_invalid_current_tag() -> None:
    with pytest.raises(release_workflow.WorkflowError, match="Invalid release tag"):
        release_workflow.previous_tag("v1.2", ["v1.1.0"])


def test_first_publication_creates_release() -> None:
    responses = [result(1, stderr="release not found\n"), result()]
    with patch.object(release_workflow.subprocess, "run", side_effect=responses) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", Path("notes.md"), ["dist/a", "dist/b"])
    assert runner.call_args_list[1].args == (
        (
            "gh",
            "release",
            "create",
            "v1.2.3",
            "dist/a",
            "dist/b",
            "--verify-tag",
            "--title",
            "pratfall 1.2.3",
            "--notes-file",
            "notes.md",
        ),
    )


def test_retry_updates_release_and_replaces_assets() -> None:
    responses = [result(stdout="v1.2.3\n"), result(), result()]
    with patch.object(release_workflow.subprocess, "run", side_effect=responses) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", Path("notes.md"), ["dist/a"])
    assert runner.call_args_list[1].args == (
        (
            "gh",
            "release",
            "edit",
            "v1.2.3",
            "--title",
            "pratfall 1.2.3",
            "--notes-file",
            "notes.md",
        ),
    )
    assert runner.call_args_list[2].args == (
        ("gh", "release", "upload", "v1.2.3", "dist/a", "--clobber"),
    )


def test_release_lookup_error_does_not_attempt_creation() -> None:
    with (
        patch.object(
            release_workflow.subprocess,
            "run",
            return_value=result(1, stderr="HTTP 500: server error\n"),
        ) as runner,
        pytest.raises(release_workflow.WorkflowError, match="HTTP 500"),
    ):
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", Path("notes.md"), ["dist/a"])
    assert runner.call_count == 1
