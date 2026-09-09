import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

SEMVER_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class WorkflowError(Exception):
    pass


def version(tag: str) -> tuple[int, int, int] | None:
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def previous_tag(current: str, tags: Sequence[str]) -> str | None:
    current_version = version(current)
    if current_version is None:
        raise WorkflowError(f"Invalid release tag: {current!r}")
    candidates = [
        (parsed, tag)
        for tag in tags
        if (parsed := version(tag)) is not None and parsed < current_version
    ]
    return max(candidates)[1] if candidates else None


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkflowError(f"{' '.join(args)} failed ({result.returncode}). {detail}")
    return result


def release_exists(tag: str) -> bool:
    result = command(
        "gh", "release", "view", tag, "--json", "tagName", "--jq", ".tagName", check=False
    )
    if result.returncode == 0:
        if result.stdout.strip() != tag:
            raise WorkflowError(f"GitHub returned an unexpected release tag for {tag}.")
        return True
    if result.returncode == 1 and "release not found" in result.stderr.lower():
        return False
    detail = (result.stderr or result.stdout).strip()
    raise WorkflowError(f"gh release view {tag} failed ({result.returncode}). {detail}")


def publish(tag: str, title: str, notes: Path, assets: Sequence[str]) -> None:
    if release_exists(tag):
        command("gh", "release", "edit", tag, "--title", title, "--notes-file", str(notes))
        command("gh", "release", "upload", tag, *assets, "--clobber")
        return
    command(
        "gh",
        "release",
        "create",
        tag,
        *assets,
        "--verify-tag",
        "--title",
        title,
        "--notes-file",
        str(notes),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    previous = subparsers.add_parser("previous-tag")
    previous.add_argument("current")
    publication = subparsers.add_parser("publish")
    publication.add_argument("tag")
    publication.add_argument("title")
    publication.add_argument("notes", type=Path)
    publication.add_argument("assets", nargs="+")
    args = parser.parse_args()
    if args.operation == "previous-tag":
        tags = command("git", "tag", "--list").stdout.splitlines()
        if selected := previous_tag(args.current, tags):
            print(selected)
        return
    publish(args.tag, args.title, args.notes, args.assets)


if __name__ == "__main__":
    try:
        main()
    except (WorkflowError, OSError, KeyboardInterrupt) as error:
        print(f"Release workflow stopped: {error}", file=sys.stderr)
        sys.exit(1)
