# Getting started

Pratfall needs an installed, authenticated CLI for each agent you want to use. Set up each agent
through its native instructions first; Pratfall inherits that setup.

## Install

Install with [Homebrew](https://brew.sh), which also provides Python:

```sh
brew install nielsmadan/tap/pratfall
```

Without Homebrew, install with [uv](https://docs.astral.sh/uv/) and Python 3.13+. This installs
the latest `main`; append a release tag such as `@v1.0.0` to pin a release:

```sh
uv tool install git+https://github.com/nielsmadan/pratfall
```

Then check which agent commands are available:

```sh
prat doctor
```

`doctor` checks which agent commands are available on `PATH`. It does not launch them or check
credentials. Once your agent is ready, run a prompt:

```sh
prat cx "summarize this checkout"
```

Here, `cx` is the alias for Codex. Run `prat agents` to see all supported agents.

## Run from a checkout

To try unreleased changes, run from a source checkout with uv and prefix commands with `uv run`:

```sh
uv sync
uv run prat --help
```

To use the checkout as the `prat` command, run one of these recipes from it:

| Command | Effect |
| --- | --- |
| `just install` | Install or replace the checkout as a user-level uv tool, even at the same version. |
| `just install-editable` | Install a command that uses the source checkout directly. |
| `just uninstall` | Remove the tool installation. |

For development setup and checks, see [Contributing](../../CONTRIBUTING.md).

## Check agent versions

```sh
prat doctor --versions
```

This runs each available configured command with its version arguments: `version` for Amp,
`--version` for other agents. Each probe has empty stdin, a three-second deadline, and a 64 KiB
limit per output stream. Configured wrappers run too, so their normal side effects apply.

Probe failures appear in the report; they do not fail the inventory command. An interruption
stops the remaining probes. Version checks do not verify authentication.

## Next steps

- [Choose an agent](agents.md) and check its supported settings.
- [Run prompts](running.md) from text, files, or pipes.
- [Create profiles](profiles.md) to reuse settings across runs and projects.
