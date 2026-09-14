# Pratfall

<img src="assets/logo.svg" alt="Pratfall logo" width="128" height="128">

Pratfall provides the `prat` command for one-shot coding-agent invocations through named profiles.
Claude Code, Codex, Gemini, Antigravity, Copilot, Kiro, Cursor, OpenClaw, Hermes, OpenCode,
OpenHands, Warp (Oz), iFlow, Qwen Code, Amp, Reasonix, Droid, Kimi CLI, Mistral Vibe, Crush,
Devin, and Cortex Code (CoCo) execution are available.

OpenHands requires existing native setup and its headless mode automatically approves actions.
iFlow 0.5.19 also defaults to automatic approval for noninteractive prompts unless native settings
or explicit modes override it. Warp uses the legacy `oz` command, documented through September
2026; iFlow requires existing custom-API configuration after its hosted service retirement. See
[agents and limitations](docs/user/agents.md) for supported controls and lifecycle details.
Kimi CLI 1.50.0 print mode automatically approves tools; Droid exec defaults to read-only. Vibe
inherits its native agent (default accepts edits) and denies headless approval callbacks. Vibe
model/effort overrides are unsupported; choose its model through existing native settings or a
trusted command prefix. Prat does not substitute the successor kimi-code for kimi-cli.
Crush local run automatically approves actions; `CRUSH_CLIENT_SERVER` can select a server backend
with a separate lifetime. Devin print requires an already trusted workspace. Cortex exec requires
existing Snowflake account/connection/authentication, disables plan mode and rejects interactive asks.

- [Installation](#installation)
- [Running prompts](#running-prompts)
- [Output and diagnostics](#output-and-diagnostics)
- [Profiles and configuration](#profiles-and-configuration)
- [Agent controls and accounting](#agent-controls-and-accounting)
- [Reference guides](#reference-guides)

## Installation

Requires Python 3.13 or newer. Development uses [uv](https://docs.astral.sh/uv/) and
[just](https://github.com/casey/just):

```sh
uv sync
uv run prat --help
uv run prat agents
uv run prat doctor
uv run prat doctor --versions
just check
just coverage
just build
```

Until the first release is published, use the source checkout as shown above. `just install`
installs or replaces the checkout as a user-level uv tool, including at the same version;
`just install-editable` links the source instead, and `just uninstall` removes the installation.
These recipes change the user's tool installation and are never run implicitly. Pratfall does not
install or authenticate native agent commands.

## Running prompts

Run an agent by its full name, an alias, or a named profile with exactly one prompt. Every agent
has a full name; names of four letters or fewer need no alias. See the
[complete selector map](docs/user/agents.md).

```sh
uv run prat codex "summarize the changes in this checkout"
uv run prat --model gpt-5.6-luna simple --effort low --prompt="review this code"
printf 'multiline\nprompt\n' | uv run prat cc -
printf 'redirected prompt\n' | uv run prat cc
uv run prat cx --file request.md
uv run prat cc --max-budget-usd 1 --max-turns 3 "inspect this failure"
uv run prat ag --effort high "finish the requested change"
uv run prat oc --model provider/model "review this repository"
uv run prat kiro --model claude-sonnet-4 --effort high "inspect this change"
uv run prat claw --model provider/model "run the focused tests"
uv run prat hm --effort high "review this repository"
uv run prat cx --fast "review this urgently"
```

Run flags can appear before or after the selector. `--fast` and `--no-fast` override supported
native fast settings for one Claude or Codex invocation. Omitting both preserves the native setting;
`--no-fast` is an explicit override. Supply exactly one explicit prompt source:
positional text, `--prompt=TEXT`, `-f PATH` / `--file PATH`, or stdin through positional `-` or
`--file -`. `--prompt=TEXT` is required for text that starts with a dash, and `--prompt=-` remains
literal text. With no explicit source, Prat reads stdin when it is redirected; at a terminal it
reports how to provide a prompt instead of asking interactively. An explicit source takes
precedence over incidental redirected stdin, which is left unread.

All prompt sources must contain UTF-8 text that is not empty or whitespace-only, has no NUL bytes,
and fits within 1 MiB. Prat reads at most one extra byte to detect overflow. Named files must be
regular files; symlinks to regular files work, while directories and special files are rejected.
Relative prompt-file paths resolve from the directory where `prat` was invoked, independently of
`--cwd`. Missing, unreadable, invalid, oversized, and unavailable standard input exits 2 without
launching an agent.

Prat sends Claude, Codex, Antigravity, OpenCode, Qwen, Amp, Reasonix, Droid, Kimi, Vibe and Crush prompts
through native stdin protocols. Reasonix, Kimi and Vibe trim surrounding whitespace natively.
Crush receives the original input and adds two trailing newline characters natively.
OpenClaw, Hermes and Cortex use native stdin file options. Gemini, Copilot, OpenHands, Warp and iFlow use
native prompt options, while Cursor, Kiro and Devin use positional prompts after an end-of-options marker.
These argv transports are subject to the operating system's argv-size limit, which can be lower than
Prat's 1 MiB input limit. Prompts are never passed through a shell or the inherited terminal.

Use `--cwd PATH` to select the agent working directory. Relative config and working-directory
paths resolve from the directory where `prat` was invoked. Configuration, selected native options,
and `--cwd` are validated before reading prompt input. Input acquisition waits for EOF and is
not charged to the agent timeout. SIGINT or SIGTERM during input acquisition exits as interrupted
without launching an agent. `--timeout` is a wall-clock deadline for agent execution and output
draining, and defaults to 600 seconds. A timed-out or failed run can leave edits in the working
directory. A later manual rerun starts a fresh invocation; Pratfall does not automatically retry or
roll back edits.

`--dry-run` validates the complete invocation and shows its argv without launching the agent.
Prompts carried through stdin appear only as a byte count. Gemini, Copilot, Cursor, Kiro, OpenHands,
Warp, iFlow and Devin carry the prompt in argv, so their previews include it:

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

## Output and diagnostics

Standard output contains normalized final text. Structured adapters select assistant text;
text adapters capture complete native stdout, which can include banners or progress.
Launch/completion progress and native stderr diagnostics go to standard error.
If writing diagnostics fails, Prat cleans up the owned process group and preserves the answer on
stdout. JSON results also retain accounting and report `output_io_error` unless a prior timeout,
interruption, or runner error takes precedence.
An unexpected failure anywhere in a run is normalized the same way and reported as
`internal_error` after the owned process group is cleaned up; once a result has reached stdout it is
reported on stderr alone.

`--json` emits exactly one normalized result object for a run,
including validation and runtime failures:

```json
{
  "schema_version": 1,
  "agent": "codex",
  "profile": "simple",
  "model": "gpt-5.6-luna",
  "reported_models": null,
  "cost_usd": null,
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

Add `--progress` to a run for bounded elapsed-time and activity updates on stderr. Updates use
static categories, never native payload text, and are coalesced to at most one line per second with
a heartbeat at least every five seconds. A blocked stderr consumer does not stall the agent
deadline. JSON mode still emits exactly one final object on stdout.

Run statuses are `success`, `error`, `timeout`, or `interrupted`. Invalid arguments, config, and
prompt input exit 2; missing and non-executable commands exit 127 and 126; timeouts exit 124; interruption
exits `128 + signal`; native nonzero codes are otherwise preserved. Provider, protocol, output,
and I/O failures exit 1. A dry-run JSON object instead has `dry_run: true`, resolved identity,
`argv`, `cwd`, `timeout`, and `stdin_bytes`; it does not claim a run status or usage.

Codex, Copilot, Antigravity, OpenCode, Warp, Qwen, Amp and Kimi JSONL is decoded incrementally. OpenHands uses a
separate incremental decoder for SDK events mixed with native status and summary text. Each physical event and
the retained answer/protocol state are limited to 8 MiB, with at most 16,384 retained logical
records. Discarded events have no cumulative trace limit. Whole-document JSON and text adapters
retain the 8 MiB complete stdout limit; native stderr is limited to 2 MiB. Limit failures are
explicit and trigger process-group cleanup. Every whole-document JSON adapter applies the same
strictness rule and reports `protocol_error`, or `output_encoding` for an unpaired surrogate, when
native output carries a duplicate object key, a nonfinite number, an over-wide numeric literal, or
excessive nesting anywhere in the document, including in fields that adapter does not read.
Duplicate keys, over-wide numeric literals and excessive nesting are rejected while parsing, while
the nonfinite and unpaired-surrogate checks walk a successfully-shaped document, so a provider or
protocol failure detected earlier is reported instead.

`agents` lists canonical names, aliases, and supported settings. `doctor` reports which executable
prefixes are available on PATH without launching them. `doctor --versions` additionally executes
each available configured argv prefix followed by its version arguments (`version` for Amp,
`--version` for other agents), one at a time, with empty stdin, a
three-second deadline, and 64 KiB limits on each output stream. Configured wrappers are trusted and
may have side effects. Version failures remain per-agent diagnostics and do not make doctor fail;
an interruption stops later probes and exits with `128 + signal`. Neither form checks credentials.
Missing optional agents do not make the inventory command fail. `profiles` lists resolved settings.

## Profiles and configuration

Global configuration lives at `$XDG_CONFIG_HOME/pratfall/config.toml`, or
`~/.config/pratfall/config.toml` when XDG_CONFIG_HOME is absent or empty. Prat also reads a
TOML `.pratfile` in the invocation directory and merges it over the global configuration.
It does not search parent directories; `--cwd` only changes the agent's working directory.
Use `--config PATH` before or after a selector or management command to select a local file
instead of `.pratfile`. Relative paths resolve from the invocation directory. The selected
file is still merged over the global configuration.

```sh
uv run prat config path
uv run prat --config .pratfile config init
uv run prat --config example.toml config init
uv run prat --config example.toml config validate
uv run prat --config example.toml profiles --json
```

`config init` creates missing directories with owner-only permissions and exclusively creates an
owner-readable and writable example file; it fails if the file already exists. Ordinary
commands never write configuration. `config path` and `config init` target the global file
unless `--config PATH` is supplied. `config validate` checks the effective merged configuration
and reports the loaded files. Missing global and automatic local files are valid; a missing
explicitly selected file is an error. Existing files require `version = 1` and use the same schema:

```toml
version = 1

[defaults]
timeout = 600
# fast = true

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
When upgrading, [rename profiles that collide with newly reserved selectors](docs/user/profiles.md).

Global and local `[defaults]` merge field by field, with local values taking precedence.
Local `[agents.NAME].command` arrays replace the global command for that agent.
Profiles from both files are available. When both define the same profile name, the entire
local profile replaces the global profile, and Prat prints a warning naming the profile and
both files to stderr, including with `--json`. Omitted local profile fields inherit the merged
defaults. Relative executable paths remain relative to the file that defines the command.
Validation errors identify the source of inherited settings. A local path aliasing the global
file loads it once, preserving the selected path's base for relative commands.

## Agent controls and accounting

Options are `model`, `effort`, `fast`, `timeout` (seconds), `native_args` (an argv array), and supported
native budgets: Claude and Vibe `max_budget_usd` and `max_turns`, Copilot `max_ai_credits`, and
Hermes/Qwen/Cortex `max_turns`. Qwen forwards its native session-turn limit.
Limits must be positive and finite; `max_turns` must be an integer. Unsupported options and
unknown fields fail validation. Model IDs are passed through for agents that support model
selection. Gemini and Cursor effort overrides are unsupported. Kiro
accepts effort `low|medium|high|xhigh|max`; Hermes accepts
`none|minimal|low|medium|high|xhigh|max|ultra`. Native effort enums are validated where known;
Droid accepts the provider-specific union `none|dynamic|off|minimal|low|medium|high|xhigh|max`.
Cortex accepts `minimal|low|medium|high|max`.
Other supported effort strings are left to the native CLI. Native permission defaults are
preserved unless explicitly overridden.

Fast mode is verified only for Claude and Codex. Claude receives an invocation-only inline
`fastMode` setting; Codex receives an invocation-only `service_tier` setting (`priority` for true,
`default` for false). Pratfall never edits either tool's settings files, changes models, or promises
that the selected account can use faster service. Unsupported agents reject both true and false.

Antigravity stdin stream mode requires native version 1.1.15 or later. Its documented
`--print-timeout` behavior applies to print mode and is not verified for stdin streaming, so
Pratfall enforces the configured outer deadline without forwarding that native option.

Claude's `max_budget_usd` is its native API-call budget in US dollars, and `max_turns` is its
native agent-turn limit. Prat passes both through without treating either as a token cap. Claude's
documentation does not specify exact overshoot behavior, so `max_budget_usd` should not be treated
as a stronger spend guarantee than the native CLI provides. Codex has no verified invocation-wide
token or spend cap, so Prat rejects budget fields for Codex. Copilot's `max_ai_credits` is a soft
per-response native limit, and Prat does not reinterpret it as a hard run-wide cap. Vibe maps
`max_budget_usd` to `--max-price` in dollars and interrupts when native usage exceeds the limit;
its native `--max-tokens` counts cumulative prompt/completion tokens and is available only after
`--`. Prat forwards these native limits without strengthening their guarantees.

Native usage is nullable. Gemini sums each model's reported token snapshot and maps fresh input,
cache reads, candidates, and thoughts without adding the duplicated per-role views. Antigravity
uses the cumulative usage snapshot from its single terminal result. OpenCode keeps the latest
snapshot for each step ID, sums distinct steps, and reports its native fresh-input, cache-read,
cache-write, text-output, and reasoning counts separately. OpenClaw maps its documented input and
output token counts. Copilot 1.0.83 exposes AI-credit and duration metrics but no token counts in
CLI JSON, while Cursor, Kiro text mode, and Hermes quiet mode do not provide token usage, so those
adapters report `usage: null` rather than inferred values.

Qwen and Amp use the last native result's usage without summing assistant-message snapshots.
Reasonix maps input/output and cache-read counts; its cache-creation field means cache misses,
so cache-write counts stay null. Reasonix's cost USD alias can carry another currency, so it
also stays null. Qwen and Amp expose no verified USD total. Reasonix model selection names a
configured provider, and its supported effort values depend on that provider. Amp has no generic
model, effort or budget override; its native mode is not a model identifier. Amp's native default
approves tools unless existing settings enable permissions. Reasonix paused recovery is reported
as incomplete even when its native exit code is zero.

Crush (`cr`), Devin (`dv`) and Cortex (`co`) return bounded complete native stdout with terminal
CR/LF removed. Text can include banners/progress; native exit status determines success, including
empty successful output. Prat does not infer errors or accounting from prose. All three support
model selection; Cortex also supports effort and native turn limits. Optional native arguments
are Crush `--verbose`/`-v` and `--debug`/`-d`, Devin `--permission-mode`, and Cortex
`--connection`/`-c` for an existing Snowflake connection. No approval or trust bypass is injected.

The requested `model` remains separate from `reported_models`, which contains distinct native
model identifiers in observed order when the supported protocol exposes them. Claude reports the
keys of `modelUsage`, Gemini the keys of `stats.models`, Copilot root completed-message models,
OpenClaw its provider/model identity, and Qwen root assistant-message models. Other adapters report
null. `cost_usd` is the native USD cost
from Claude or OpenClaw, or the sum of OpenCode's latest snapshot for each step ID. Missing native
data stays null, including an OpenCode run where any latest step cost is unknown. Zero is preserved.
Pratfall does not calculate prices, convert Copilot credits, or promise that native cost equals a
subscription bill.

Kiro headless mode requires native API-key authentication; follow the
[Kiro authentication guide](https://kiro.dev/docs/getting-started/authentication/#api-key-authentication-cli)
before invoking it. Its documented text mode may include banners or tool progress on stdout, so
Pratfall returns the complete native text and cannot guarantee the same answer/progress separation
as structured adapters. OpenClaw's embedded `agent exec` keeps normal native config and credential
behavior. Its native timeout accepts whole seconds, so Pratfall rounds the native hint upward while
enforcing the exact configured wall deadline itself. Explicit `--fallback` native arguments require
a model override and remain native OpenClaw behavior; Pratfall does not retry. Hermes uses quiet
chat mode and never enables its `-z` permission bypass implicitly.

Scalar precedence is invocation overrides, then the selected profile, then local defaults,
then global defaults. Native argument arrays replace the lower-precedence array, including when
the replacement is empty. Executable prefixes
come only from `[agents.NAME]`. Relative executable paths containing `/` are resolved relative to
the config file; bare executable names use PATH. Arguments are kept as literal strings, without
shell interpolation, variable expansion, or tilde expansion. All effective profiles are validated
even when another selector is used. Unused executables need not be installed.

Management `--json` emits one object with `schema_version: 1`. Agent and doctor inventories use
`agents`; profile listings use `profiles`; config operations report `path` plus their result.
Config validation also reports `sources`, the loaded files in global-to-local order; its `path`
is the highest-precedence loaded file, or the global path when neither file exists.
Doctor records contain nullable `version` and `version_error` fields in addition to availability.
Errors return status `error`, `exit_code`, and `error` with a stable `code` and readable `message`.
Invalid syntax or configuration exits with 2. Help and version output remain text.

## Reference guides

Detailed references live alongside this README:

- [Installation](docs/user/getting-started.md) and [running prompts](docs/user/running.md)
- [Profiles and configuration](docs/user/profiles.md)
- [Agent selectors, capabilities, and limitations](docs/user/agents.md)
- [JSON output](docs/user/json.md)
- [Developer and release documentation](docs/overview.md)

Pratfall is MIT licensed; see [LICENSE](LICENSE). Published package and Homebrew URLs will become
installable only after the first GitHub release.
