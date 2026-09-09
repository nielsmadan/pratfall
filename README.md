# Pratfall

Pratfall provides the `prat` command for one-shot coding-agent invocations through named profiles.
Claude Code and Codex execution are available in this development increment. The inventory also
lists the eight adapters planned for later increments; attempting to run one of those exits with
an `unsupported_agent` error.

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

Run a built-in selector or a named profile with exactly one prompt:

```sh
uv run prat cx "summarize the changes in this checkout"
uv run prat --model gpt-5.6-luna simple --effort low --prompt="review this code"
printf 'multiline\nprompt\n' | uv run prat cc -
uv run prat cc --max-budget-usd 1 --max-turns 3 "inspect this failure"
```

Run flags can appear before or after the selector. `--prompt=TEXT` is required for prompt text
that starts with a dash. A lone `-` reads one UTF-8 prompt from stdin. Prompts must be nonempty,
contain no NUL bytes, and fit within 1 MiB. Prat sends both Claude and Codex prompts through
stdin, never through a shell or the inherited terminal.

Use `--cwd PATH` to select the agent working directory. Relative config and working-directory
paths resolve from the directory where `prat` was invoked. `--timeout` is a wall-clock deadline
for the child and output draining, and defaults to 600 seconds.

`--dry-run` validates the complete invocation and shows its argv without launching the agent or
printing the prompt:

```sh
uv run prat simple "review this change" --dry-run
uv run prat simple "review this change" --dry-run --json
```

Native arguments after `--` replace configured `native_args` for that invocation. Prat accepts a
finite set of documented flags and rejects native positional arguments plus flags that could
replace its prompt, structured output, model, effort, budget, session, or working-directory
contract. Explicit native permission choices remain available:

```sh
uv run prat cx "inspect only" -- --sandbox read-only --ephemeral
uv run prat cc "make the requested edit" -- --permission-mode acceptEdits
```

Standard output contains only the final assistant answer. Launch/completion progress and native
diagnostics go to standard error. `--json` emits exactly one normalized result object for a run,
including validation and runtime failures:

```json
{
  "schema_version": 1,
  "agent": "codex",
  "profile": "simple",
  "model": "gpt-5.6-luna",
  "status": "success",
  "output": "Final answer",
  "exit_code": 0,
  "native_exit_code": 0,
  "duration_ms": 1234,
  "usage": {
    "input_tokens": 100,
    "cached_input_tokens": 50,
    "cache_write_input_tokens": null,
    "output_tokens": 20,
    "reasoning_output_tokens": null
  },
  "error": null
}
```

Run statuses are `success`, `error`, `timeout`, or `interrupted`. Invalid arguments and config
exit 2; missing and non-executable commands exit 127 and 126; timeouts exit 124; interruption
exits `128 + signal`; native nonzero codes are otherwise preserved. Provider, protocol, output,
and I/O failures exit 1. A dry-run JSON object instead has `dry_run: true`, resolved identity,
`argv`, `cwd`, `timeout`, and `stdin_bytes`; it does not claim a run status or usage.

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

Claude's `max_budget_usd` is its native API-call budget in US dollars, and `max_turns` is its
native agent-turn limit. Prat passes both through without treating either as a token cap. Claude's
documentation does not specify exact overshoot behavior, so `max_budget_usd` should not be treated
as a stronger spend guarantee than the native CLI provides. Codex has no verified invocation-wide
token or spend cap, so Prat rejects budget fields for Codex.

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
