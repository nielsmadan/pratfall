# Pratfall

<img src="assets/logo.svg" alt="Pratfall logo" width="128" height="128">

One command to run your coding agents. Pratfall wraps installed agent CLIs with
[short aliases](#aliases), [common parameters](#common-parameters), and reusable
[local and global profiles](#profiles).

```sh
prat cx "review this change"
prat cc --effort high "investigate this failure"
```

## Installation

Requires Python 3.13+ and an installed, authenticated native agent CLI. Until the first release,
run from a source checkout with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run prat --help
uv run prat doctor
```

Prefix the examples below with `uv run`, or use `just install` to install the checkout as a
user-level `prat` command. See [getting started](docs/user/getting-started.md) for setup details.

## Aliases

Choose an agent by name or short alias:

| Agent | Name | Alias |
| --- | --- | --- |
| Claude Code | `claude` | `cc` |
| Codex | `codex` | `cx` |
| Gemini | `gemini` | `gm` |
| Copilot | `copilot` | `cp` |
| Cursor | `cursor` | `cu` |
| OpenCode | `opencode` | `oc` |

These are a few of the supported agents. Run `prat agents` for the full list and supported
settings, or see [all selectors and agent limitations](docs/user/agents.md).
Names of four letters or fewer, such as `kiro`, `qwen`, and `amp`, use the name itself.

## Common parameters

Use the same flags across agents, before or after the selector:

```sh
prat cx --model gpt-5.6-luna --effort low "review this change"
prat cc --file request.md --timeout 120 --progress
printf 'summarize this checkout\n' | prat gm
```

| Parameter | Purpose |
| --- | --- |
| `--model MODEL` | Select a native model. |
| `--effort EFFORT` | Set reasoning effort. |
| `--fast` / `--no-fast` | Override fast mode for Claude or Codex. |
| `--timeout SECONDS` | Set the execution deadline; default: 600 seconds. |
| `--cwd PATH` | Choose the agent's working directory. |
| `-f PATH` / `--file PATH` | Read a prompt from a UTF-8 file; `-` reads stdin. |
| `--json` | Return one JSON result with output, status, and available usage. |
| `--progress` | Show live activity on stderr. |
| `--trace` | Copy captured native stdout to stderr. |
| `--dry-run` | Preview the resolved invocation without launching the agent. |

Model, effort, fast mode, and budget support depend on the agent; unsupported settings are
rejected. Native budgets are available through `--max-budget-usd`, `--max-turns`, and
`--max-ai-credits` where supported.

Pass one prompt as text, a file, or redirected stdin. Use `--prompt=TEXT` for text starting with
a dash. Supported native arguments go after `--`, for example:

```sh
prat cx "inspect this change" -- --sandbox read-only
```

Prat preserves native authentication and permission defaults. See
[running prompts](docs/user/running.md) for input rules and run behavior, and
[JSON output](docs/user/json.md) for result fields and errors.

## Profiles

Save an agent and its settings under a name, then use that name like an alias.
Both global and local configuration use the same TOML format:

```toml
version = 1

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
```

```sh
prat simple "review this change"
prat simple --effort high "investigate this failure"
```

| Scope | Config file | Create an example |
| --- | --- | --- |
| Global | `~/.config/pratfall/config.toml`¹ | `prat config init` |
| Local | `.pratfile` in the invocation directory | `prat --config .pratfile config init` |

¹ Uses `$XDG_CONFIG_HOME/pratfall/config.toml` when `XDG_CONFIG_HOME` is set and nonempty.

Global profiles are available across projects. Local configuration merges over global
configuration; a local profile with the same name replaces the entire global profile.
Prat reads `.pratfile` only in the invocation directory, without searching parent directories.
`--cwd` does not change config discovery; `--config PATH` selects a different local file.

Settings resolve in this order: **command-line flags → profile → local defaults → global defaults**.
Use `[defaults]` for shared settings, `prat profiles` to inspect resolved profiles, and
`prat config validate` to check configuration.

See [profiles and configuration](docs/user/profiles.md) for defaults, command wrappers, and
merging rules.

## More

- [User guides](docs/user/overview.md)
- [Contributing](CONTRIBUTING.md) and [developer documentation](docs/overview.md)
- [MIT license](LICENSE)
