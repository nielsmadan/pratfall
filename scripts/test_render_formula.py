import subprocess
from pathlib import Path

import pytest

from render_formula import render


def test_formula_renders_release_source_without_runtime_resources(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    sha256 = "a" * 64
    formula = render("1.2.3", sha256, root / "packaging/homebrew/pratfall.rb.tmpl")
    path = tmp_path / "pratfall.rb"
    path.write_text(formula)
    result = subprocess.run(["ruby", "-c", str(path)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "tags/v1.2.3.tar.gz" in formula
    assert f'  sha256 "{sha256}"' in formula
    assert 'depends_on "python@3.13"' in formula
    assert "resource " not in formula


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "01.2.3", "1.2.3;bad"])
def test_formula_rejects_invalid_versions(version: str) -> None:
    root = Path(__file__).resolve().parent.parent
    with pytest.raises(ValueError, match="Invalid release version"):
        render(version, "a" * 64, root / "packaging/homebrew/pratfall.rb.tmpl")


def test_formula_rejects_placeholder_checksum() -> None:
    root = Path(__file__).resolve().parent.parent
    with pytest.raises(ValueError, match="64 lowercase"):
        render("1.2.3", "SHA256", root / "packaging/homebrew/pratfall.rb.tmpl")
