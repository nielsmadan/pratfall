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

check-static:
    @uv run ruff check
    @uv run ruff format --check
    @uv run pylint src/pratfall
    @uv run mypy

check: check-static
    @uv run pytest tests/ scripts/ -q

build:
    @rm -rf dist build
    @uv build --sdist
    @uv build --wheel dist/pratfall-*.tar.gz

install:
    @uv tool install --reinstall --force .
    @echo "Installed: $(command -v prat)"

install-editable:
    @uv tool install --reinstall --force --editable .
    @echo "Installed (editable): $(command -v prat)"

uninstall:
    @uv tool uninstall pratfall

clean:
    @rm -rf dist build .pytest_cache htmlcov .coverage coverage.xml

changelog:
    @uvx {{git_cliff}} -o CHANGELOG.md

# Run the demo's commands with real agents and save their output to docs/demo/takes
demo-record:
    docs/demo/record.sh

# Render assets/demo.gif by replaying the take saved by demo-record
demo:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v vhs >/dev/null || { echo "install vhs first"; exit 1; }
    [ -x /usr/bin/jq ] || { echo "the replay needs /usr/bin/jq (macOS 15 or later)"; exit 1; }
    [ -f docs/demo/takes/script.sha256 ] || { echo "no take recorded; run just demo-record first"; exit 1; }
    script=$(sed -n '/^# commands:begin$/,/^# commands:end$/p' docs/demo/demo.tape |
        sed -n 's/^Type `\(.*\)`$/\1/p')
    [ "$(printf '%s' "$script" | shasum -a 256 | cut -c1-64)" = "$(cat docs/demo/takes/script.sha256)" ] ||
        { echo "demo.tape changed since the take was recorded; run just demo-record"; exit 1; }
    case "$(vhs --version)" in
        *0.12.0*) echo "vhs 0.12.0 writes no output; upgrade to 0.12.1 or later"; exit 1 ;;
    esac
    export PRAT_DEMO_DIR="$PWD/docs/demo" PRAT_DEMO_MARKER="$PWD/.cache/demo-replayed"
    rm -f .cache/demo.mp4 "$PRAT_DEMO_MARKER" "$PRAT_DEMO_MARKER.missing"
    vhs docs/demo/demo.tape
    if [ -f "$PRAT_DEMO_MARKER.missing" ]; then
        echo "no recorded take for:"; cat "$PRAT_DEMO_MARKER.missing"; echo "run just demo-record first"; exit 1
    fi
    [ -f "$PRAT_DEMO_MARKER" ] || { echo "the replay shim never ran"; exit 1; }
    ffmpeg -y -loglevel error -i .cache/demo.mp4 -vf \
        "mpdecimate=hi=64*2:lo=64:frac=0.1,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff:max_colors=64[p];[b][p]paletteuse=diff_mode=rectangle:dither=none" \
        -final_delay 500 assets/demo.gif
    echo "Wrote assets/demo.gif ($(du -h assets/demo.gif | cut -f1))"

[positional-arguments]
release *args:
    @curtains "$@"
