import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
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


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"),
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def release_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Release test")
    git(repository, "config", "user.email", "release@example.invalid")
    (repository / "source").write_text("main\n", encoding="utf-8")
    git(repository, "add", "source")
    git(repository, "commit", "-m", "feat: initial")
    git(repository, "tag", "-a", "v1.2.3", "-m", "Release v1.2.3")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repository, git(repository, "rev-parse", "v1.2.3^{commit}")


def test_validate_tag_requires_annotated_tag_for_event_commit_on_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, event_sha = release_repository(tmp_path)
    monkeypatch.chdir(repository)
    assert release_workflow.validate_tag("v1.2.3", event_sha) == git(
        repository, "rev-parse", "HEAD"
    )


def test_validate_tag_cli_accepts_the_push_event_commit_sha(tmp_path: Path) -> None:
    repository, event_sha = release_repository(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "validate-tag", "v1.2.3", event_sha],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == event_sha


def test_validate_tag_rejects_mismatched_event_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _event_sha = release_repository(tmp_path)
    (repository / "source").write_text("later\n", encoding="utf-8")
    git(repository, "add", "source")
    git(repository, "commit", "-m", "fix: later")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.chdir(repository)
    with pytest.raises(release_workflow.WorkflowError, match="does not identify"):
        release_workflow.validate_tag("v1.2.3", git(repository, "rev-parse", "HEAD"))


def test_validate_tag_rejects_lightweight_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _event_sha = release_repository(tmp_path)
    git(repository, "tag", "v1.2.4")
    monkeypatch.chdir(repository)
    with pytest.raises(release_workflow.WorkflowError, match="annotated tag"):
        release_workflow.validate_tag("v1.2.4", git(repository, "rev-parse", "v1.2.4"))


def test_validate_tag_rejects_commit_outside_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _event_sha = release_repository(tmp_path)
    git(repository, "checkout", "--orphan", "unmerged")
    (repository / "source").write_text("unmerged\n", encoding="utf-8")
    git(repository, "add", "source")
    git(repository, "commit", "-m", "feat: unmerged")
    git(repository, "tag", "-a", "v2.0.0", "-m", "Release v2.0.0")
    monkeypatch.chdir(repository)
    with pytest.raises(release_workflow.WorkflowError, match="not contained"):
        release_workflow.validate_tag("v2.0.0", git(repository, "rev-parse", "v2.0.0"))


def publication_files(tmp_path: Path) -> tuple[Path, list[str], str]:
    notes = tmp_path / "notes.md"
    notes.write_text("Release notes\n", encoding="utf-8")
    wheel = tmp_path / "pratfall.whl"
    sdist = tmp_path / "pratfall.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    return notes, [str(wheel), str(sdist)], "a" * 40


def asset(path: str) -> dict[str, object]:
    data = Path(path).read_bytes()
    return {
        "name": Path(path).name,
        "size": len(data),
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "state": "uploaded",
    }


def metadata(
    notes: Path,
    assets: list[str],
    target: str,
    *,
    draft: bool = False,
) -> dict[str, object]:
    return {
        "tagName": "v1.2.3",
        "name": "pratfall 1.2.3",
        "body": notes.read_text(encoding="utf-8"),
        "targetCommitish": target,
        "isDraft": draft,
        "isPrerelease": False,
        "isImmutable": False,
        "assets": [asset(path) for path in assets],
    }


def test_first_publication_pins_target_and_creates_release(tmp_path: Path) -> None:
    notes, assets, target = publication_files(tmp_path)
    responses = [result(1, stderr="release not found\n"), result()]
    with patch.object(release_workflow.subprocess, "run", side_effect=responses) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_args_list[1].args == (
        (
            "gh",
            "release",
            "create",
            "v1.2.3",
            *assets,
            "--verify-tag",
            "--target",
            target,
            "--title",
            "pratfall 1.2.3",
            "--notes-file",
            str(notes),
        ),
    )


def test_retry_accepts_identical_release_without_mutation(tmp_path: Path) -> None:
    notes, assets, target = publication_files(tmp_path)
    response = result(stdout=json.dumps(metadata(notes, assets, target)))
    with patch.object(release_workflow.subprocess, "run", return_value=response) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_count == 1
    assert "isPrerelease" in runner.call_args.args[0][-1].split(",")


def test_partial_publication_uploads_only_missing_asset_without_clobber(tmp_path: Path) -> None:
    notes, assets, target = publication_files(tmp_path)
    responses = [result(stdout=json.dumps(metadata(notes, assets[:1], target))), result()]
    with patch.object(release_workflow.subprocess, "run", side_effect=responses) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_args_list[1].args == (("gh", "release", "upload", "v1.2.3", assets[1]),)


def test_matching_draft_is_published_after_missing_assets_upload(tmp_path: Path) -> None:
    notes, assets, target = publication_files(tmp_path)
    responses = [
        result(stdout=json.dumps(metadata(notes, assets[:1], target, draft=True))),
        result(),
        result(),
    ]
    with patch.object(release_workflow.subprocess, "run", side_effect=responses) as runner:
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_args_list[2].args == (("gh", "release", "edit", "v1.2.3", "--draft=false"),)


@pytest.mark.parametrize("field", ["name", "body", "targetCommitish", "tagName"])
def test_retry_rejects_divergent_release_metadata(tmp_path: Path, field: str) -> None:
    notes, assets, target = publication_files(tmp_path)
    release = metadata(notes, assets, target)
    release[field] = "different"
    with (
        patch.object(
            release_workflow.subprocess, "run", return_value=result(stdout=json.dumps(release))
        ) as runner,
        pytest.raises(release_workflow.WorkflowError, match=field),
    ):
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_count == 1


@pytest.mark.parametrize(
    ("draft", "value"), [(False, True), (True, True), (False, None), (False, "false"), (False, 0)]
)
def test_retry_rejects_prerelease_or_unavailable_prerelease_state(
    tmp_path: Path, draft: bool, value: object
) -> None:
    notes, assets, target = publication_files(tmp_path)
    release = metadata(notes, assets, target, draft=draft)
    if value is None:
        del release["isPrerelease"]
    else:
        release["isPrerelease"] = value
    with (
        patch.object(
            release_workflow.subprocess, "run", return_value=result(stdout=json.dumps(release))
        ) as runner,
        pytest.raises(release_workflow.WorkflowError, match="non-prerelease"),
    ):
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_count == 1


@pytest.mark.parametrize("value", [None, "sha256:" + "0" * 64])
def test_retry_rejects_unverifiable_or_divergent_asset_digest(
    tmp_path: Path, value: str | None
) -> None:
    notes, assets, target = publication_files(tmp_path)
    release = metadata(notes, assets, target)
    release["assets"][0]["digest"] = value
    with (
        patch.object(
            release_workflow.subprocess, "run", return_value=result(stdout=json.dumps(release))
        ),
        pytest.raises(release_workflow.WorkflowError, match="divergent bytes"),
    ):
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)


def test_release_lookup_error_does_not_attempt_creation(tmp_path: Path) -> None:
    notes, assets, target = publication_files(tmp_path)
    with (
        patch.object(
            release_workflow.subprocess,
            "run",
            return_value=result(1, stderr="HTTP 500: server error\n"),
        ) as runner,
        pytest.raises(release_workflow.WorkflowError, match="HTTP 500"),
    ):
        release_workflow.publish("v1.2.3", "pratfall 1.2.3", notes, target, assets)
    assert runner.call_count == 1


def test_all_workflow_actions_use_full_commit_pins() -> None:
    workflows = SCRIPT.parent.parent / ".github/workflows"
    for path in workflows.glob("*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s+[^@\s]+@([^\s]+)", line)
            if match is not None:
                assert re.fullmatch(r"[0-9a-f]{40}", match[1]), f"{path}: {line}"
