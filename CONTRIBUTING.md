# Contributing to Pratfall

Pratfall is a small, solo-maintained project. Issues and pull requests are welcome and handled on a
best-effort basis.

Install [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just), and
[Lefthook](https://lefthook.dev/installation/homebrew), then prepare the checkout:

```sh
just setup
just doctor
just check
just coverage
just docs-build
just build
```

`just setup` installs repository dependencies and Lefthook hooks. It does not install `prat` as a
global command. Tests use fake executables and must not invoke paid agent inference.

Runtime code lives in `src/pratfall/`; tests mirror its modules under `tests/`. Release helper tests
live under `scripts/` and are part of `just test` and `just check`. Keep the runtime standard-library
only unless a dependency has a demonstrated need.

Use one of `feat:`, `fix:`, or `chore:` without scopes. `feat` and `fix` entries appear in the
generated changelog. Mark breaking changes with `feat!:` / `fix!:` or a `BREAKING CHANGE:` footer.

Report bugs through [GitHub issues](https://github.com/nielsmadan/pratfall/issues). Report security
issues privately as described in [SECURITY.md](SECURITY.md).
