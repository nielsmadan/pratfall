import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).with_name("release.py")
SPEC = importlib.util.spec_from_file_location("release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class Checkout:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.root = directory / "checkout"
        self.remote = directory / "origin.git"
        self.root.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.command("git", "init", "--bare", str(self.remote), cwd=directory)
        self.command("git", "init", "-b", "main")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release@example.invalid")
        self.git("remote", "add", "origin", str(self.remote))
        (self.root / "scripts").mkdir()
        shutil.copyfile(SCRIPT, self.root / "scripts/release.py")
        (self.root / "scripts/prepare.py").write_text(
            "from pathlib import Path\nimport sys\nPath('VERSION').write_text(sys.argv[1] + '\\n')\n"
        )
        self.config = {
            "name": "Fixture",
            "branch": "main",
            "components": 3,
            "initial_version": "0.1.0",
            "tools": ["git", "python3"],
            "checks": [],
            "publication": "Version tag for fixture consumers",
            "stages": [
                {
                    "files": ["VERSION"],
                    "message": "chore: release {version}",
                    "commands": [[sys.executable, "scripts/prepare.py", "{version}"]],
                }
            ],
        }
        self.save_config()
        (self.root / "VERSION").write_text("1.2.0\n")
        self.commit("chore: initial fixture")
        self.git("tag", "-a", "v1.2.0", "-m", "Fixture release")
        (self.root / "source.txt").write_text("fixed\n")
        self.commit("fix: correct behavior")
        self.git("push", "origin", "main", "--tags")
        self.initial_head = self.git("rev-parse", "HEAD")

    def command(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            args,
            cwd=cwd or self.root,
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def git(self, *args: str) -> str:
        return self.command("git", *args)

    def commit(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-m", message)

    def save_config(self) -> None:
        (self.root / "scripts/release.json").write_text(json.dumps(self.config))

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/release.py", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            input="",
            capture_output=True,
            check=False,
        )


@pytest.fixture
def checkout(tmp_path: Path) -> Checkout:
    return Checkout(tmp_path)


def test_version_policy() -> None:
    cases = [
        ((1, 2, 0), ["fix: repair"], 3, "1.2.1"),
        ((1, 2, 0), ["feat: add"], 3, "1.3.0"),
        ((1, 2, 0), ["feat!: replace"], 3, "2.0.0"),
        ((0, 2, 0), ["fix: change\n\nBREAKING CHANGE: wire format"], 3, "0.3.0"),
        ((1, 2, 0), ["chore: dependencies"], 3, None),
    ]
    for base, messages, components, expected in cases:
        assert release.suggested(base, messages, components)[0] == expected


def test_dry_run_preserves_checkout_and_remote(checkout: Checkout) -> None:
    result = checkout.invoke("--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Proposed:  v1.2.1" in result.stdout
    assert checkout.git("status", "--porcelain") == ""
    assert checkout.git("tag", "--list") == "v1.2.0"
    assert checkout.git("rev-parse", "HEAD") == checkout.initial_head


def test_release_prepares_tags_and_pushes_atomically(checkout: Checkout) -> None:
    checkout.git("tag", "-a", "experiment", "-m", "Unreviewed local tag")
    checkout.git("config", "push.followTags", "true")
    result = checkout.invoke("--yes")
    assert result.returncode == 0, result.stderr
    assert checkout.git("show", "v1.2.1:VERSION") == "1.2.1"
    remote_tags = checkout.command("git", "--git-dir", str(checkout.remote), "tag", "--list")
    assert remote_tags.splitlines() == ["v1.2.0", "v1.2.1"]


def test_dirty_checkout_stops_before_release(checkout: Checkout) -> None:
    (checkout.root / "unrelated.txt").write_text("user work\n")
    checkout.git("add", "unrelated.txt")
    result = checkout.invoke("--yes")
    assert result.returncode == 1
    assert "must be clean" in result.stderr
    assert checkout.git("diff", "--cached", "--name-only") == "unrelated.txt"
    assert checkout.git("tag", "--list") == "v1.2.0"


def test_missing_remote_main_is_rejected(checkout: Checkout) -> None:
    checkout.git("push", "origin", ":refs/heads/main")
    result = checkout.invoke("--dry-run")
    assert result.returncode == 1
    assert "origin/main does not exist" in result.stderr


def test_remote_advance_is_rejected(checkout: Checkout) -> None:
    other = checkout.directory / "other"
    checkout.command("git", "clone", "-b", "main", str(checkout.remote), str(other))
    checkout.command("git", "config", "user.name", "Other author", cwd=other)
    checkout.command("git", "config", "user.email", "other@example.invalid", cwd=other)
    (other / "advance.txt").write_text("remote change\n")
    checkout.command("git", "add", ".", cwd=other)
    checkout.command("git", "commit", "-m", "fix: remote change", cwd=other)
    checkout.command("git", "push", "origin", "main", cwd=other)
    result = checkout.invoke("--yes")
    assert result.returncode == 1
    assert "include origin/main" in result.stderr
    assert checkout.git("rev-parse", "HEAD") == checkout.initial_head


def test_local_only_older_release_tag_is_rejected(checkout: Checkout) -> None:
    first_commit = checkout.git("rev-list", "--max-parents=0", "HEAD")
    checkout.git("tag", "v1.1.0", first_commit)
    result = checkout.invoke("--dry-run")
    assert result.returncode == 1
    assert "Local and origin release tags differ" in result.stderr


def test_remote_only_older_release_tag_is_rejected(checkout: Checkout) -> None:
    checkout.command(
        "git", "--git-dir", str(checkout.remote), "tag", "v1.1.0", checkout.initial_head
    )
    result = checkout.invoke("--dry-run")
    assert result.returncode == 1
    assert "Local and origin release tags differ" in result.stderr


def test_divergent_older_release_tag_is_rejected(checkout: Checkout) -> None:
    first_commit = checkout.git("rev-list", "--max-parents=0", "HEAD")
    checkout.git("tag", "v1.1.0", first_commit)
    checkout.git("push", "origin", "refs/tags/v1.1.0")
    checkout.command(
        "git",
        "--git-dir",
        str(checkout.remote),
        "update-ref",
        "refs/tags/v1.1.0",
        checkout.initial_head,
    )
    result = checkout.invoke("--dry-run")
    assert result.returncode == 1
    assert "Local v1.1.0 does not match origin" in result.stderr


def test_unrelated_tags_are_ignored(checkout: Checkout) -> None:
    checkout.git("tag", "v1.2.1-rc1")
    checkout.command(
        "git", "--git-dir", str(checkout.remote), "tag", "version-2", checkout.initial_head
    )
    result = checkout.invoke("--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Proposed:  v1.2.1" in result.stdout


def test_unexpected_preparation_path_is_rejected(checkout: Checkout) -> None:
    checkout.config["stages"][0]["commands"].append(
        [sys.executable, "-c", "from pathlib import Path; Path('other').write_text('bad')"]
    )
    checkout.save_config()
    checkout.commit("chore: configure fixture")
    result = checkout.invoke("--yes")
    assert result.returncode == 1
    assert "unexpected files: other" in result.stderr
    assert checkout.git("tag", "--list") == "v1.2.0"


def test_publication_waits_for_matching_tag_and_reports_release(checkout: Checkout) -> None:
    config = {"workflow": "release.yml", "repository": "owner/repo"}
    responses = [
        json.dumps(
            [
                {"databaseId": 1, "headBranch": "other", "url": "other"},
                {"databaseId": 2, "headBranch": "v1.2.1", "url": "workflow-url"},
            ]
        ),
        json.dumps({"status": "completed", "conclusion": "success"}),
        "release-url",
    ]
    with (
        patch.object(release, "run", side_effect=responses) as runner,
        patch("sys.stdout", new_callable=io.StringIO) as output,
    ):
        release.publication(checkout.root, config, "v1.2.1", "revision")
    assert "Released: release-url" in output.getvalue()
    assert "2" in runner.call_args_list[1].args


def test_publication_failure_is_an_error(checkout: Checkout) -> None:
    config = {"workflow": "release.yml", "repository": "owner/repo"}
    responses = [
        json.dumps([{"databaseId": 2, "headBranch": "v1.2.1", "url": "workflow-url"}]),
        json.dumps({"status": "completed", "conclusion": "failure"}),
    ]
    with (
        patch.object(release, "run", side_effect=responses),
        pytest.raises(release.ReleaseError, match="failure: workflow-url"),
    ):
        release.publication(checkout.root, config, "v1.2.1", "revision")
