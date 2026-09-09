import re
import sys
from pathlib import Path

VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
EXPECTED_ARGUMENT_COUNT = 4
FORMULA_VERSION_PATTERN = re.compile(
    r'^\s*url "https://github\.com/nielsmadan/pratfall/archive/refs/tags/v([^"/]+)\.tar\.gz"$',
    re.MULTILINE,
)


def render(version: str, sha256: str, template: Path) -> str:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid release version: {version!r}")
    if not SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("Source SHA256 must be 64 lowercase hexadecimal characters.")
    content = template.read_text()
    if content.count("{{VERSION}}") != 1 or content.count("{{SHA256}}") != 1:
        raise ValueError("Formula template must contain one version and one SHA256 placeholder.")
    return content.replace("{{VERSION}}", version).replace("{{SHA256}}", sha256)


def installed_version(content: str) -> tuple[int, int, int]:
    matches = FORMULA_VERSION_PATTERN.findall(content)
    if len(matches) != 1 or VERSION_PATTERN.fullmatch(matches[0]) is None:
        raise ValueError("Existing Homebrew formula has no unique trusted Pratfall version.")
    return tuple(int(part) for part in matches[0].split("."))


def render_file(version: str, sha256: str, template: Path, output: Path) -> bool:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid release version: {version!r}")
    if not SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("Source SHA256 must be 64 lowercase hexadecimal characters.")
    release_version = tuple(int(part) for part in version.split("."))
    if output.exists() and installed_version(output.read_text()) > release_version:
        return False
    content = render(version, sha256, template)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    return True


def main() -> None:
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        raise SystemExit("usage: render_formula.py VERSION SHA256 OUTPUT")
    root = Path(__file__).resolve().parent.parent
    output = Path(sys.argv[3])
    rendered = render_file(
        sys.argv[1],
        sys.argv[2],
        root / "packaging/homebrew/pratfall.rb.tmpl",
        output,
    )
    print("rendered" if rendered else "skipped-newer")


if __name__ == "__main__":
    main()
