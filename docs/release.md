# Release and distribution

The [release helper](../scripts/release.py#L251) prepares an annotated tag, atomically pushes `main`
and that tag, then prints workflow and release links and finishes. Publication runs asynchronously;
local success confirms the Git push. The [workflow](../.github/workflows/release.yml) attaches the
wheel and sdist and updates `nielsmadan/homebrew-tap`. Pratfall is not published to PyPI.

- [Prerequisites](#prerequisites)
- [Prepare and publish](#prepare-and-publish)
- [Verify artifacts before publication](#verify-artifacts-before-publication)
- [Check installed packages locally](#check-installed-packages-locally)
- [Retry a partial publication](#retry-a-partial-publication)
- [Update Homebrew](#update-homebrew)

## Prerequisites

Run from a clean `main` checkout with full history, one matching fetch/push destination, matching
local and remote release tags, and all remote commits incorporated. The repository and `origin/main`
must already exist. Publish a new repository's initial branch as a separate explicit action; retain
the helper's remote-branch and ancestry checks. The local helper uses Git for remote inspection and
publication of the branch and tag, with Git push access to the repository.

Check that origin's actual host is `github.com`: the helper's
[repository check](../scripts/release.py#L92) matches a URL substring rather than parsing its host.
This validation limitation was deferred in the 2026-09-09 review.

Before the first release, configure `HOMEBREW_TAP_TOKEN` as a repository Actions secret using a
fine-grained PAT restricted to write access on `nielsmadan/homebrew-tap`. The workflow can create an
absent `Formula/pratfall.rb` from the [template](../packaging/homebrew/pratfall.rb.tmpl). It renders
the placeholders only after downloading tagged GitHub source and computing its real SHA256.

## Prepare and publish

```sh
just release --dry-run
just release
just release patch
just release 0.2.0
```

`--dry-run` performs read-only Git and remote inspection and prints the planned checks. A real
release runs the [configured checks](../scripts/release.json) before interactive confirmation or
explicit `--yes`, then verifies HEAD, checkout and remote state still match the reviewed state.

[Preparation:189](../scripts/release.py#L189) updates `pyproject.toml`, runs `uv lock`, and generates
`CHANGELOG.md` with the pinned git-cliff version. Only those three files may change. It creates a
`chore: release VERSION` commit and annotated `vVERSION` tag, then pushes them atomically with
`--no-follow-tags`. Failures preserve local state for inspection; never replace a published tag.
Check the linked workflow for publication success or failure. A failed workflow leaves the remote
tag in place for a retry after fixing its cause.

`CHANGELOG.md` is generated output. `just changelog` refreshes its unreleased view; do not edit a
generated release section by hand. Release, tagging, publication, tap changes and global command
installation each require explicit user authorization.

## Verify artifacts before publication

The workflow's verification job runs without write credentials. It requires an annotated tag whose
push-event commit is contained in fetched `origin/main`, validates the semantic tag against the
project version, and enforces Ruff, formatting, import-cycle, strict mypy and branch-coverage checks.

It builds the sdist and then the wheel from that sdist, installs both in isolated environments,
and runs [offline installed-package scenarios](../scripts/qa_installed.py). The QA report,
distributions, source checksum and generated notes are saved as one workflow artifact.

## Check installed packages locally

Run these checks after changes to parsing, configuration, adapters, normalization, or process
cleanup. The [harness](../scripts/qa_installed.py) uses fake native commands, a restricted PATH
and disposable XDG configuration. It spends no inference credits. Python assertions must be
enabled; `-O` and `PYTHONOPTIMIZE` are rejected.

The offline procedure requires an available Python 3.13 interpreter, the synced development
environment, and cached Hatchling 1.32.0 plus its dependencies. From the repository root:

```sh
mkdir -p .cache
qa_root=$(mktemp -d .cache/prat-qa.XXXXXX)
mkdir -p "$qa_root/artifacts" "$qa_root/consumer"
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/build-env"
uv pip install --offline --no-python-downloads \
  --python "$qa_root/build-env/bin/python" "hatchling==1.32.0"
uv build --offline --no-python-downloads --no-build-isolation \
  --python "$qa_root/build-env/bin/python" --sdist --out-dir "$qa_root/artifacts"
sdist=$(find "$qa_root/artifacts" -name '*.tar.gz' -type f)
uv build --offline --no-python-downloads --no-build-isolation \
  --python "$qa_root/build-env/bin/python" --wheel "$sdist" --out-dir "$qa_root/artifacts"
wheel=$(find "$qa_root/artifacts" -name '*.whl' -type f)
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/env-wheel"
uv pip install --offline --no-python-downloads --no-deps \
  --python "$qa_root/env-wheel/bin/python" "$wheel"
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/env-sdist"
uv pip install --offline --no-python-downloads \
  --python "$qa_root/env-sdist/bin/python" "hatchling==1.32.0"
uv pip install --offline --no-python-downloads --no-build-isolation --no-deps \
  --python "$qa_root/env-sdist/bin/python" "$sdist"
uv run --offline --no-python-downloads --frozen --no-sync python scripts/qa_installed.py \
  --prat "$qa_root/env-wheel/bin/prat" \
  --sdist-prat "$qa_root/env-sdist/bin/prat" \
  --wheel "$wheel" \
  --sdist "$sdist" \
  --expected-version "$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)" \
  --base-revision "$(git rev-parse HEAD)" \
  --work-dir "$qa_root/run" \
  --output "$qa_root/results.json"
```

A missing interpreter or cache entry is a failed prerequisite. Populate caches separately with
network access, then restart the offline procedure in a new directory:

```sh
uv sync --frozen
prep_root=$(mktemp -d .cache/prat-qa-prep.XXXXXX)
uv venv --python 3.13 "$prep_root"
uv pip install --python "$prep_root/bin/python" "hatchling==1.32.0"
```

Keep the offline flags on the verification commands. Read their complete exit status and result
JSON. Matching source/archive/install runtime manifests and artifact hashes identify what was
tested. Keep results with release artifacts; remove disposable environments after checking cleanup.
See [test isolation and evidence](execution.md#test-isolation-and-evidence) for coverage boundaries.

An offline sdist installation can reuse uv's cached wheel. To check the source build itself,
preinstall the backend dependencies and add
`--no-cache --reinstall --offline --no-index --no-deps --no-build-isolation` to
`uv pip install --python ENV/bin/python SDIST`. Confirm that output reports building the sdist.

## Retry a partial publication

A separate minimal `contents: write` job checks out the immutable triggering ref without persisted
credentials, revalidates the tag, and consumes the verified artifact. It uses `gh` through
`scripts/release_workflow.py` to manage GitHub release records, draft state, and uploaded assets,
which Git cannot do.
[Publication checks:133](../scripts/release_workflow.py#L133) require existing title, notes, target
commit, non-prerelease state and attached asset hashes to match. Identical assets stay in place;
missing assets are uploaded without clobbering. A matching partial draft is published only after
its assets are complete. Divergent or unverifiable state stops the workflow.

## Update Homebrew

The formula uses Homebrew `python@3.13` and checksummed pure-Python wheels for Hatchling and its
runtime requirements. It installs those resources first, then Pratfall with build isolation disabled.

[Formula rendering:32](../scripts/render_formula.py#L32) parses the trusted tap formula version:
an older tag cannot replace a newer formula; a same-version repair or normal upgrade can proceed.
Rendering, Ruby validation and commit creation run before the PAT-bearing final push. That command
receives its credential through a Git header, never a clone URL.
