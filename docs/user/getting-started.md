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

## Enable shell completion

`prat` completes subcommands, agent names and aliases, profiles, workflows, template names after
`-t`, `--param` names for the selected profile, template or workflow, effort levels for the
selected agent, flags, and file or directory paths for options that take one. In zsh, each name
shows a short description. Profiles, workflows and templates come from the global config and the
current directory's `.pratfile`, or from a `--config` typed earlier on the line.

The Homebrew formula installs the completion files. Zsh loads them when Homebrew's
`share/zsh/site-functions` is on `FPATH` before `compinit` runs; bash needs the `bash-completion@2`
package. See Homebrew's [shell completion guide](https://docs.brew.sh/Shell-Completion).

With any other install, add one line to your shell rc file. In `~/.zshrc`, put it after
`compinit`:

```zsh
eval "$(prat completion zsh)"
```

In `~/.bashrc`:

```bash
eval "$(prat completion bash)"
```

Without an argument, `prat completion` picks the shell from `$SHELL` and falls back to bash. Only
bash and zsh are supported, and the command rejects `--json`.

Completion never launches an agent, and it fails silently: an invalid config leaves only the
built-in names, so run `prat config validate` when your profiles stop appearing. Values in the
`--flag=value` form are not completed, and `completion` is reserved as a profile name.

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
