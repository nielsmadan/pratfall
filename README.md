# Pratfall

Pratfall provides the `prat` command for one-shot coding-agent invocations through named profiles.
Claude Code, Codex, Gemini, Antigravity, Copilot, Kiro, Cursor, OpenClaw, Hermes, and OpenCode
execution are available.

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

Until the first release is published, use the source checkout as shown above. `just install-local`
installs the checkout as a user-level uv tool; `just refresh-local` performs a no-cache reinstall,
and `just reset-local` removes it. These recipes change the user's tool installation and are never
run implicitly. Pratfall does not install or authenticate native agent commands.

Run a built-in selector or a named profile with exactly one prompt:

```sh
uv run prat cx "summarize the changes in this checkout"
uv run prat --model gpt-5.6-luna simple --effort low --prompt="review this code"
printf 'multiline\nprompt\n' | uv run prat cc -
printf 'redirected prompt\n' | uv run prat cc
uv run prat cx --file request.md
uv run prat cc --max-budget-usd 1 --max-turns 3 "inspect this failure"
uv run prat ag --effort high "finish the requested change"
uv run prat oc --model provider/model "review this repository"
uv run prat ki --model claude-sonnet-4 --effort high "inspect this change"
uv run prat claw --model provider/model "run the focused tests"
uv run prat hm --effort high "review this repository"
```

Run flags can appear before or after the selector. Supply exactly one explicit prompt source:
positional text, `--prompt=TEXT`, `-f PATH` / `--file PATH`, or stdin through positional `-` or
`--file -`. `--prompt=TEXT` is required for text that starts with a dash, and `--prompt=-` remains
literal text. With no explicit source, Prat reads stdin when it is redirected; at a terminal it
reports how to provide a prompt instead of asking interactively. An explicit source takes
precedence over incidental redirected stdin, which is left unread.

All prompt sources must contain UTF-8 text that is not empty or whitespace-only, has no NUL bytes,
and fits within 1 MiB. Prat reads at most one extra byte to detect overflow. Named files must be
regular files; symlinks to regular files work, while directories and special files are rejected.
Relative prompt-file paths resolve from the directory where `prat` was invoked, independently of
`--cwd`. Missing, unreadable, invalid, and oversized input exits 2 without launching an agent.

Prat sends Claude, Codex, Antigravity, and OpenCode prompts through native stdin protocols.
OpenClaw and Hermes use native stdin file options. Gemini and Copilot use a documented prompt
option, while Cursor and Kiro use positional prompts after an end-of-options marker. These four
argv transports are subject to the operating system's argv-size limit, which can be lower than
Prat's 1 MiB input limit. Prompts are never passed through a shell or the inherited terminal.

Use `--cwd PATH` to select the agent working directory. Relative config and working-directory
paths resolve from the directory where `prat` was invoked. Input acquisition waits for EOF and is
not charged to the agent timeout. SIGINT or SIGTERM during input acquisition exits as interrupted
without launching an agent. `--timeout` is a wall-clock deadline for agent execution and output
draining, and defaults to 600 seconds. A timed-out or failed run can leave edits in the working
directory.

`--dry-run` validates the complete invocation and shows its argv without launching the agent.
Prompts carried through stdin appear only as a byte count. Gemini, Copilot, Cursor, and Kiro carry
the prompt in argv, so their previews include it:

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

Run statuses are `success`, `error`, `timeout`, or `interrupted`. Invalid arguments, config, and
prompt input exit 2; missing and non-executable commands exit 127 and 126; timeouts exit 124; interruption
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

`config init` creates missing directories with owner-only permissions and exclusively creates an
owner-readable and writable example file; it fails if the file already exists. Ordinary
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
selection. Gemini and Cursor effort overrides are unsupported. Kiro
accepts effort `low|medium|high|xhigh|max`; Hermes accepts
`none|minimal|low|medium|high|xhigh|max|ultra`. Native effort enums are validated where known;
other supported effort strings are left to the native CLI. Native permission defaults are
preserved unless explicitly overridden.

Claude's `max_budget_usd` is its native API-call budget in US dollars, and `max_turns` is its
native agent-turn limit. Prat passes both through without treating either as a token cap. Claude's
documentation does not specify exact overshoot behavior, so `max_budget_usd` should not be treated
as a stronger spend guarantee than the native CLI provides. Codex has no verified invocation-wide
token or spend cap, so Prat rejects budget fields for Codex. Copilot's `max_ai_credits` is a soft
per-response native limit, and Prat does not reinterpret it as a hard run-wide cap.

Native usage is nullable. Gemini sums each model's reported token snapshot and maps fresh input,
cache reads, candidates, and thoughts without adding the duplicated per-role views. Antigravity
uses the cumulative usage snapshot from its single terminal result. OpenCode keeps the latest
snapshot for each step ID, sums distinct steps, and reports its native fresh-input, cache-read,
cache-write, text-output, and reasoning counts separately. OpenClaw maps its documented input and
output token counts. Copilot 1.0.83 exposes AI-credit and duration metrics but no token counts in
CLI JSON, while Cursor, Kiro text mode, and Hermes quiet mode do not provide token usage, so those
adapters report `usage: null` rather than inferred values.

Kiro headless mode requires native API-key authentication; follow the
[Kiro authentication guide](https://kiro.dev/docs/getting-started/authentication/#api-key-authentication-cli)
before invoking it. Its documented text mode may include banners or tool progress on stdout, so
Pratfall returns the complete native text and cannot guarantee the same answer/progress separation
as structured adapters. OpenClaw's embedded `agent exec` keeps normal native config and credential
behavior. Its native timeout accepts whole seconds, so Pratfall rounds the native hint upward while
enforcing the exact configured wall deadline itself. Explicit `--fallback` native arguments require
a model override and remain native OpenClaw behavior; Pratfall does not retry. Hermes uses quiet
chat mode and never enables its `-z` permission bypass implicitly.

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

The [user guides](docs/user/index.md) cover installation, profiles, prompts, JSON, and adapter
limitations. Builder documentation starts at [docs/overview.md](docs/overview.md). Pratfall is MIT
licensed; see [LICENSE](LICENSE). Published package and Homebrew URLs will become installable only
after the first GitHub release.
