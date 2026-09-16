# Getting started

Pratfall requires Python 3.13+ and an installed, authenticated CLI for each agent you want to use.
Set up each agent through its native instructions first; Pratfall inherits that setup.

## Run from a checkout

Until a release is published, run from a source checkout with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run prat --help
uv run prat doctor
```

`doctor` checks which agent commands are available on `PATH`. It does not launch them or check
credentials. Once your agent is ready, run a prompt:

```sh
uv run prat cx "summarize this checkout"
```

Here, `cx` is the alias for Codex. Run `uv run prat agents` to see all supported agents.

## Install the command

To use `prat` without the `uv run` prefix, run one of these recipes from the checkout:

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
