# Release and distribution

Pratfall's release helper prepares an annotated tag, atomically pushes `main` and that tag, waits
for the matching GitHub Actions run, and reports the resulting GitHub release. It never publishes
to PyPI. The workflow attaches the wheel and sdist and creates or replaces the Pratfall formula in
`nielsmadan/homebrew-tap`.

## First publication prerequisites

The repository must be published and `origin/main` must exist before `just release` can run. For a
new repository, publish the initial main branch as a separate, explicit action first. Do not weaken
the helper's remote-branch or ancestry checks to bootstrap it.

Before the first release, configure the repository's `HOMEBREW_TAP_TOKEN` Actions secret with write
access to `nielsmadan/homebrew-tap`. The workflow can bootstrap an absent
`Formula/pratfall.rb`. Its template contains placeholders, so no fake checksum is presented as an
installable formula. The workflow renders the formula only after downloading the tagged GitHub
source and computing its SHA256.

GitHub Pages deployment is prepared in `.github/workflows/docs.yml`. After publishing, enable
Pages with GitHub Actions as the source. No custom domain is configured.

## Release command

Run from a clean `main` checkout with full history, matching fetch and push URLs, matching local and
remote release tags, and all remote commits incorporated:

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
local state for inspection; never replace a published tag.

The tag workflow validates the exact semantic tag against the project version, tests the installed
project, builds the sdist and then builds the wheel from that sdist, generates release notes without
ignoring git-cliff failures, and uploads both artifacts. Homebrew authentication is supplied only to
the final tap push through a command-scoped Git header; credentials are not embedded in a clone URL.

The formula uses Homebrew `python@3.13` and `virtualenv_install_with_resources`. Pratfall has no
runtime dependencies, so it needs no resource blocks. Homebrew performs the isolated package build.

`CHANGELOG.md` is generated output. `just changelog` refreshes its unreleased view; do not edit a
generated release section by hand.
