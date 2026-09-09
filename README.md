# Pratfall

Pratfall provides the `prat` command for one-shot coding-agent invocations through named profiles.
This first development increment provides configuration and management commands. Agent execution
and dry-run command construction follow in the next increment.

Requires Python 3.13 or newer. Development uses [uv](https://docs.astral.sh/uv/) and
[just](https://github.com/casey/just):

```sh
uv sync
uv run prat --help
uv run prat agents
uv run prat doctor
just check
just coverage
just build
```

`agents` lists canonical names, aliases, and supported settings. `doctor` reports which executable
prefixes are available on PATH; it never launches agents or checks credentials. Missing optional
agents do not make the inventory command fail. `profiles` lists resolved profile settings.

Configuration lives at `$XDG_CONFIG_HOME/pratfall/config.toml`, or
`~/.config/pratfall/config.toml` when XDG_CONFIG_HOME is absent or empty. Use `--config PATH`
before or after management commands to select another file:

```sh
uv run prat config path
uv run prat --config example.toml config init
uv run prat --config example.toml config validate
uv run prat --config example.toml profiles --json
```

`config init` exclusively creates an example file and fails if it already exists. Ordinary
commands never write configuration. A missing default file is valid; a missing explicitly
selected file is an error. Existing files require `version = 1`.

```toml
version = 1

[defaults]
timeout = 600

[agents.codex]
command = ["codex"]

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
```

Profile names use letters, digits, underscores, or hyphens, starting with a letter or digit.
Built-in names, aliases, and management commands are reserved. Each profile requires an agent;
there is no profile inheritance. Agent settings accept canonical names and a `command` array
only. Profiles accept canonical agent names or aliases.

Options are `model`, `effort`, `timeout` (seconds), `native_args` (an argv array), and supported
native budgets: Claude `max_budget_usd` and `max_turns`, Copilot `max_ai_credits`, and Hermes
`max_turns`.
Limits must be positive and finite; `max_turns` must be an integer. Unsupported options and
unknown fields fail validation. Model IDs are passed through for agents that support model
selection. Kiro model selection and Gemini, Cursor, and Hermes effort overrides are unsupported.
Native effort enums are validated where known; other supported effort strings are left to the
native CLI. Native permission defaults are preserved unless explicitly overridden.

Scalar precedence is invocation overrides, then profile, then defaults. Native argument arrays
replace the lower-precedence array, including when the replacement is empty. Executable prefixes
come only from `[agents.NAME]`. Relative executable paths containing `/` are resolved relative to
the config file; bare executable names use PATH. Arguments are kept as literal strings, without
shell interpolation, variable expansion, or tilde expansion. All profiles are validated even
when another selector is used. Unused executables need not be installed.

Management `--json` emits one object with `schema_version: 1`. Agent and doctor inventories use
`agents`; profile listings use `profiles`; config operations report `path` plus their result.
Errors return status `error`, `exit_code`, and `error` with a stable `code` and readable `message`.
Invalid syntax or configuration exits with 2. Help and version output remain text.
