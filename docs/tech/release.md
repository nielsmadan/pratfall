# Release and distribution

Pratfall's release helper prepares an annotated tag, atomically pushes `main` and that tag, then
prints workflow and release links and finishes. Publication runs asynchronously; local success
confirms the Git push. The GitHub workflow attaches the wheel and sdist and creates or replaces the
Pratfall formula in `nielsmadan/homebrew-tap`. It never publishes to PyPI.

## First publication prerequisites

The repository must be published and `origin/main` must exist before `just release` can run. For a
new repository, publish the initial main branch as a separate, explicit action first. Do not weaken
the helper's remote-branch or ancestry checks to bootstrap it.

Before the first release, configure the repository's `HOMEBREW_TAP_TOKEN` Actions secret from a
fine-grained PAT restricted to write access on `nielsmadan/homebrew-tap` only. The workflow can
bootstrap an absent
`Formula/pratfall.rb`. Its template contains placeholders, so no fake checksum is presented as an
installable formula. The workflow renders the formula only after downloading the tagged GitHub
source and computing its SHA256.

GitHub Pages deployment is prepared in `.github/workflows/docs.yml`. After publishing, enable
Pages with GitHub Actions as the source. No custom domain is configured.

## Release command

Run from a clean `main` checkout with full history, matching fetch and push URLs, matching local and
remote release tags, and all remote commits incorporated. The local helper uses Git for remote
inspection and publication of the branch and tag, with Git push access to the repository:

```sh
just release --dry-run
just release
just release patch
just release 0.2.0
```

`--dry-run` performs read-only Git and remote inspection and prints planned checks. A real release
requires an interactive confirmation or explicit `--yes`. All checks run before confirmation, and
the helper then verifies that HEAD, the checkout, and remote state still match the reviewed state.

The preparation stage updates `pyproject.toml`, runs `uv lock`, and generates `CHANGELOG.md` with
`git-cliff@2.13.1`. Only those three files may change. It creates a `chore: release VERSION` commit,
an annotated `vVERSION` tag, and uses one atomic push with `--no-follow-tags`. Failures preserve
local state for inspection; never replace a published tag. Check the linked workflow for publication
success or failure. A failed workflow leaves the remote tag in place for a retry after fixing its
cause.

The tag workflow first runs without write credentials. It requires an annotated tag whose push-event
commit is contained in fetched `origin/main`, validates the semantic tag against the project version,
and enforces Ruff, formatting, import-cycle, strict mypy, branch-coverage, and strict-docs checks. It
builds the sdist and then the wheel from that sdist, installs both artifacts in isolated environments,
and runs all offline installed-package scenarios. The QA report is saved with the distributions and
generated notes as one workflow artifact.

A separate minimal `contents: write` job checks out the immutable triggering ref without persisted
credentials and consumes that saved artifact. It uses `gh` through `scripts/release_workflow.py` to
manage GitHub release records, draft state, and uploaded assets, which Git cannot do. Release retries
require the existing title, notes, target commit, non-prerelease state, and attached asset hashes to
match. Identical assets are retained, missing assets are uploaded without clobbering, and a matching
partial draft is published only after its assets are complete. Divergent or unverifiable publication
state stops the workflow.

The formula uses Homebrew `python@3.13` plus checksummed pure-Python wheels for Hatchling and all of
its runtime requirements. It installs those resources first and installs Pratfall with build
isolation disabled. Before rendering, the workflow parses the trusted tap formula version: an older
tag cannot replace a newer formula, while a same-version repair and a normal upgrade remain valid.
Formula rendering, Ruby validation, and commit creation run before the PAT-bearing final push step;
the credential is supplied only to that command through a Git header and is never put in a clone URL.

`CHANGELOG.md` is generated output. `just changelog` refreshes its unreleased view; do not edit a
generated release section by hand.
