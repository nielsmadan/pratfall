# Pratfall one-shot coding-agent runner.

git_cliff := "git-cliff@2.13.1"

[private]
default:
    @just --list

setup:
    @uv sync --all-groups
    @lefthook install
    @just doctor

doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    need() {
        if command -v "$1" >/dev/null 2>&1; then
            printf '  ok       %s\n' "$1"
        else
            printf '  MISSING  %-12s %s\n' "$1" "$2"
            fail=1
        fi
    }
    need uv "install: https://docs.astral.sh/uv/getting-started/installation/"
    need just "install: https://github.com/casey/just#installation"
    need lefthook "install: brew install lefthook"
    if [ -f "$(git rev-parse --git-path hooks/pre-commit)" ]; then
        printf '  ok       git hooks\n'
    else
        printf '  MISSING  %-12s run: just setup\n' 'git hooks'
        fail=1
    fi
    [ "$fail" -eq 0 ] && printf 'Everything in place.\n'
    exit "$fail"

test:
    @uv run pytest tests/ scripts/ -q

coverage:
    @uv run pytest --cov --cov-report=term-missing --cov-report=html -q

lint:
    @uv run ruff check

lint-imports:
    @uv run pylint src/pratfall

format:
    @uv run ruff format

typecheck:
    @uv run mypy

check:
    @uv run ruff check
    @uv run ruff format --check
    @uv run pylint src/pratfall
    @uv run mypy
    @uv run pytest tests/ scripts/ -q

build:
    @rm -rf dist build
    @uv build --sdist
    @uv build --wheel dist/pratfall-*.tar.gz

install-local:
    @uv tool install .
    @echo "Installed: $(command -v prat)"

refresh-local:
    @uv tool install --reinstall --force --no-cache .
    @echo "Refreshed: $(command -v prat)"

reset-local:
    @uv tool uninstall pratfall

clean:
    @rm -rf dist build .pytest_cache htmlcov .coverage coverage.xml site

docs-build:
    @uv run --group docs zensical build -f mkdocs.yml --strict

docs:
    @uv run --group docs zensical serve -f mkdocs.yml

changelog:
    @uvx {{git_cliff}} -o CHANGELOG.md

[positional-arguments]
release *args:
    python3 scripts/release.py "$@"
