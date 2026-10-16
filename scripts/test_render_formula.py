import subprocess
from pathlib import Path

import pytest

from render_formula import installed_version, render, render_file

TEMPLATE = (
    Path(__file__).resolve().parent.parent / "packaging/homebrew/pratfall.rb.tmpl"
).read_text()


def test_formula_renders_release_source_with_pinned_build_resources(tmp_path: Path) -> None:
    sha256 = "a" * 64
    formula = render("1.2.3", sha256, TEMPLATE)
    path = tmp_path / "pratfall.rb"
    path.write_text(formula)
    result = subprocess.run(["ruby", "-c", str(path)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "tags/v1.2.3.tar.gz" in formula
    assert f'  sha256 "{sha256}"' in formula
    assert 'depends_on "python@3.13"' in formula
    assert formula.count("resource ") == 6
    assert "hatchling-1.32.0-py3-none-any.whl" in formula
    assert "venv.pip_install resources" in formula
    assert "venv.pip_install_and_link buildpath, build_isolation: false" in formula
    assert (
        'generate_completions_from_executable(bin/"prat", "completion", shells: [:bash, :zsh])'
        in formula
    )
    assert 'shell_output("#{bin}/prat completion zsh")' in formula


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "01.2.3", "1.2.3;bad"])
def test_formula_rejects_invalid_versions(version: str) -> None:
    with pytest.raises(ValueError, match="Invalid release version"):
        render(version, "a" * 64, TEMPLATE)


def test_formula_rejects_placeholder_checksum() -> None:
    with pytest.raises(ValueError, match="64 lowercase"):
        render("1.2.3", "SHA256", TEMPLATE)


@pytest.mark.parametrize("template", ["{{VERSION}}", "{{VERSION}} {{SHA256}} {{SHA256}}"])
def test_formula_rejects_template_without_unique_placeholders(template: str) -> None:
    with pytest.raises(ValueError, match="one version and one SHA256 placeholder"):
        render("1.2.3", "a" * 64, template)


def test_formula_version_parser_requires_one_trusted_release_url() -> None:
    assert installed_version(
        '  url "https://github.com/nielsmadan/pratfall/archive/refs/tags/v2.3.4.tar.gz"\n'
    ) == (2, 3, 4)
    with pytest.raises(ValueError, match="unique trusted"):
        installed_version('  version "2.3.4"\n')


def test_older_release_rerun_preserves_newer_formula(tmp_path: Path) -> None:
    output = tmp_path / "pratfall.rb"
    output.write_text(render("2.0.0", "b" * 64, TEMPLATE))
    original = output.read_bytes()
    assert render_file("1.9.9", "a" * 64, TEMPLATE, output) is False
    assert output.read_bytes() == original


@pytest.mark.parametrize("current", ["1.2.3", "1.2.2"])
def test_same_or_newer_release_renders_formula(tmp_path: Path, current: str) -> None:
    output = tmp_path / "pratfall.rb"
    output.write_text(render(current, "b" * 64, TEMPLATE))
    assert render_file("1.2.3", "a" * 64, TEMPLATE, output) is True
    assert "v1.2.3.tar.gz" in output.read_text()
    assert 'sha256 "' + "a" * 64 + '"' in output.read_text()


def test_existing_untrusted_formula_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "pratfall.rb"
    output.write_text("class Pratfall < Formula\nend\n")
    with pytest.raises(ValueError, match="unique trusted"):
        render_file("1.2.3", "a" * 64, TEMPLATE, output)
