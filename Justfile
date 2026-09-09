[private]
default:
    @just --list

test:
    @uv run pytest -q

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
    @uv run pytest -q

build:
    @uv build
