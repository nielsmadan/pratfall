# JSON output

Use `--json` to write one versioned result object to stdout. Diagnostics stay on stderr;
`--progress` adds bounded live activity there without changing the JSON fields.
Read fields by name: their order can differ on config or argument errors. Those errors use the
same fields when `--json` is unambiguous.

- [Conventions](#conventions)
- [Run results](#run-results)
- [Models, usage, and cost](#models-usage-and-cost)
- [Workflow results](#workflow-results)
- [Errors and partial results](#errors-and-partial-results)
- [Previews and management commands](#previews-and-management-commands)
- [Help pages](#help-pages)

## Conventions

Every `--json` output follows these rules:

- `--json` prints exactly one object on stdout, with `schema_version`, for every command that has
  a result, including help. Only `prat completion` is exempt.
- Every JSON error carries `status: "error"`, `exit_code` and `error: {code, message}`. A run error
  also carries the other run fields, set to null. Success is the exit code, or an `error` that is
  null or absent.
- Keys are snake_case and equal the config key where one exists.
- A key has one meaning and one type everywhere it appears.
- Values a script compares are lowercase tokens, such as `success`, `invalid_config` or `inline`.
  Prose appears only in `message`-like fields.
- A definition carries its own `name`. A report names what it ran by type: `agent`, `profile`,
  `workflow`, `step` or `item`.
- `null` means the agent decides. A default that Prat applies shows its value.
- Durations are integers in milliseconds with an `_ms` suffix. Settings keep their config units,
  such as `timeout` in seconds.
- `error.message` is the same text Prat prints on stderr without `--json`: lowercase, no trailing
  period, naming the flag or config key and the rejected value, such as `--effort 'ultra': Claude
  Code accepts low, medium, high, xhigh, max`. Its wording can change; match `error.code`.

## Run results

For example, `prat cx --model gpt-5.6-luna --json "review this change"` returns an object like:

```json
{
  "schema_version": 1,
  "agent": "codex",
  "profile": null,
  "model": "gpt-5.6-luna",
  "status": "success",
  "output": "Final answer",
  "exit_code": 0,
  "native_exit_code": 0,
  "duration_ms": 1234,
  "usage": null,
  "error": null,
  "reported_models": null,
  "cost_usd": null,
  "native_session_id": null,
  "structured_output": null
}
```

`status` is `success`, `error`, `timeout`, or `interrupted`. Success has `exit_code: 0` and
`error: null`. For text adapters, `output` contains complete native stdout with trailing CR/LF
removed, which may include banners and progress text.

With `-x` or `--extract`, only `output` changes to the exact body of the first fenced code block,
including its original indentation and line endings. An empty block yields `""`; a missing or
unclosed first fence leaves the entire answer unchanged. This also applies to partial answers
on failure, preserving all other fields, exit codes, and raw trace output. See
[code extraction](running.md#extract-code) for the fence grammar. Unlike ordinary text emission,
the JSON string does not receive a final newline. Dry-run previews are unaffected.

## Schema answers

`--schema PATH` requests structured output from supported agents. `output` remains a string,
containing the compact JSON answer; `structured_output` carries the same parsed JSON value.
For example, an answer can have `"output": "{\"count\":2}"` and
`"structured_output": {"count":2}`. The envelope remains `schema_version: 1`.
Ordinary and validation-error envelopes include `structured_output: null`.

JSON null is a present answer when `output` is `"null"`; false, zero, empty strings, arrays and
objects also remain distinct from missing output. Missing or malformed required output fails with
`protocol_error` (invalid Unicode uses `output_encoding`). Safe partial answers and accounting
survive errors, native nonzero exits, timeouts and interruption. No internal presence flag is emitted.
Codex selects only the last completed message and requires successful turn completion. Qwen
(v0.24+) reads native `structured_result` from the terminal success, even when its value is null;
intermediate tool arguments and child messages do not supply a final answer.
`--extract` and schema output cannot be combined. See [schema output](running.md#request-schema-output).

## Models, usage, and cost

| Field | Meaning |
| --- | --- |
| `model` | Requested model after resolving settings; null when using native defaults. |
| `reported_models` | List of distinct native model identifiers in observed order, or null if unknown. May include supporting calls, not just models that generated final text. |
| `cost_usd` | Native run cost in US dollars: a finite, nonnegative number, or null if unknown. This is not a subscription-billing guarantee. |
| `usage` | Native token counters, or null if unknown. Individual counters can also be null. |
| `native_session_id` | The session this run used: what the agent reported, else the id requested with `--session-id`, else null. Agents with the `reports_session_id` capability report one; the rest leave it null. A reported id that is blank or cannot be encoded is dropped rather than emitted. |

Unknown model and cost data stay null; Prat does not substitute the requested model or zero.
Copilot credits are not converted to USD.

The token counters are `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
`output_tokens`, and `reasoning_output_tokens`. They retain each native protocol's meaning.
Some native input totals already include cached tokens, so adding `input_tokens` and
`cached_input_tokens` can double-count them.

Agent-specific accounting rules:

- **Qwen:** models come only from root assistant messages; usage comes from the final result.
- **Amp:** uses optional final-result usage and reports no model identity or USD cost.
- **Reasonix:** `model` selects a configured provider name. It reports native input, output, and
  cache-read counts. Cache-write counts stay null because its cache-creation field means cache
  misses. `cost_usd` stays null because its cost alias may use another currency.

Goose reports native USD cost when available; its token inclusion semantics remain unverified,
so usage stays null. Kimi Code reports its returned session ID but no token or cost accounting.

Amp and Vibe have no generic model override.

## Workflow results

`prat WORKFLOW --json` returns one object for the whole [workflow](workflows.md):

```json
{
  "schema_version": 1,
  "workflow": "write",
  "status": "success",
  "exit_code": 0,
  "output": "Final text",
  "duration_ms": 5120,
  "usage": {
    "input_tokens": 1200,
    "cached_input_tokens": null,
    "cache_write_input_tokens": null,
    "output_tokens": 800,
    "reasoning_output_tokens": null
  },
  "cost_usd": 0.12,
  "error": null,
  "steps": [
    {"step": "draft", "schema_version": 1, "agent": "claude", "status": "success", "...": "..."},
    {"step": "polish", "status": "skipped"}
  ]
}
```

`output` is the joined output of the final steps, and `""` on failure or interruption. `usage` sums
each counter over the steps that report it, with a counter null when none does, and is null when
no step reports usage. `cost_usd` is likewise summed; if the aggregate overflows to a nonfinite
number, it is null while individual costs remain intact. This also applies to `each` aggregates.
`duration_ms` is the workflow's wall time.

Every declared step appears in `steps`, in declared order. A step that ran carries its
[run result](#run-results) fields after `step`; a step that never started is
`{"step": NAME, "status": "skipped"}`. A step Prat could not launch or whose child returned an
unusable result carries a synthesized result with `output: ""`, null `usage` and `cost_usd`, and
an `error`. A workflow `status` and `exit_code` are the first failed step's; on interruption they
are `interrupted` and `128 + signal`, with error code `interrupted`. An error that stops the
workflow before any step launches, such as a bad parameter, reports `steps: []` and
`duration_ms: 0`. `error.message` names the failing step as `step 'NAME': MESSAGE`. A result that
crosses the shared 64 MiB [workflow result budget](workflows.md#failure-and-interruption) is a
synthesized `stdout_limit_exceeded` failure of that step or instance. When only the joined final
output exceeds 64 MiB, the workflow fails with `stdout_limit_exceeded`, every step keeps its
result, and the message is `final output: MESSAGE`.

A step with [`each`](workflows.md#fan-out-over-items) reports only `step`, `status`, `exit_code`,
`output`, `duration_ms`, `usage`, `cost_usd`, `error`, `items` and `dropped`, in that order, with
no `schema_version`, `agent` or `profile`. `items` is an array in item order. Each entry has `item`
(the item text), `label` and, for an instance that ran, the instance's
[run result](#run-results) fields. An instance that never started is
`{"item": TEXT, "label": LABEL, "status": "skipped"}`. A failed instance's error reaches the step as
`item 'LABEL': MESSAGE`. An `each` step stopped by another step's failure before all instances
finished has status `skipped`, and an empty list gives `items: []` with status `success`. An `each`
step that never started, or that failed before its items were known, is
`{"step": NAME, "status": "skipped", "items": []}` or a synthesized error result with `items: []`
and `dropped: 0`.

`dropped` is the number of items cut by `overflow = "truncate"`, and 0 otherwise. The cut items
have no entry in `items`. The never-started entry above has no `dropped` key.

With `--dry-run --json` the preview is:

```json
{
  "schema_version": 1,
  "workflow": "write",
  "dry_run": true,
  "parameters": {"topic": "tide pools"},
  "steps": [
    {
      "step": "draft",
      "profile": "draft",
      "after": [],
      "input": "upstream",
      "bind": {},
      "each": null,
      "split": null,
      "as": null,
      "max_parallel": null,
      "max_items": null,
      "overflow": null,
      "parameters": {"topic": "tide pools"}
    }
  ]
}
```

A flag value that fails to parse, such as `--timeout abc`, is rejected before the selector is
known to be a workflow, so that error uses the plain run shape.

A `workflows` record has `name`, `parallel`, `parameters`, the defining file's absolute `source`, and `steps`,
each with `name`, `profile`, `after`, `input`, `bind`, `each`, `split`, `as`, `max_parallel`,
`max_items` and `overflow`. `each` and `as` are null when unset, and `split`, `max_parallel`,
`max_items` and `overflow` are null without `each`; with `each` they hold the effective values
(`max_parallel` 64 for `"all"`, `max_items` 4096 and `overflow` `"error"` by default). The dry-run
steps carry the same keys.

## Errors and partial results

On failure, `error` contains a stable `code` and a human-readable `message`. These are all the
stable error codes and their exit codes:

| `code` | `exit_code` | Meaning |
| --- | --- | --- |
| `invalid_config` | 2 | Configuration file is unreadable, malformed, or rejected. |
| `invalid_arguments` | 2 | Command line, prompt source, or native option is rejected. |
| `executable_not_found` | 127 | Configured command does not exist. |
| `executable_not_executable` | 126 | Configured command exists but cannot be executed. |
| `timeout` | 124 | Run deadline elapsed, or the native CLI reported its own timeout. |
| `interrupted` | `128 + signal` | Prat received SIGINT or SIGTERM, or a SIGHUP it does not ignore while running a workflow. |
| `native_signal` | `128 + signal` | The native CLI died from a signal. |
| `native_exit` | the native status, or 1 | The native CLI exited nonzero without another failure. |
| `protocol_error` | 1 | Native output violated the agent's documented protocol. |
| `provider_error` | 1 | The native CLI reported a provider or model failure. |
| `output_encoding` | 1 | Native output was not valid UTF-8 or not representable text. |
| `output_io_error` | 1 | Prat could not read agent output or write diagnostics. |
| `process_io_error` | 1 | Process setup or I/O failed before a result existed. |
| `internal_error` | 1 | Prat hit an unexpected failure and normalized it instead of aborting. |
| `stdout_limit_exceeded` | 1 | Native stdout or retained decoder state exceeded its budget. |
| `stderr_limit_exceeded` | 1 | Native stderr exceeded its budget. |
| `unsupported_platform` | 1 | Process execution requires POSIX. |

The three signal- and status-derived exit codes vary per run; all others are fixed. A native
status of 2, 124, 126, 127 or 128 and above would collide with Prat's own codes, so it exits 1;
`native_exit_code` keeps the original. Provider and protocol failures exit 1 unless a native
nonzero exit takes precedence.

A rejected option reports `invalid_arguments` when set on the command line and `invalid_config`
when set in defaults or a profile. Rejected native arguments always report `invalid_arguments`.

Failed and timed-out results retain decoded output, usage, models, and cost. Local framing,
state, encoding, and presentation failures retain decoded fields when safe to present.
Two agent-specific cases:

- **Codex:** a completed assistant message is retained as partial output if the turn never
  finishes. The missing `turn.completed` remains a protocol error.
- **Reasonix:** `recovery_paused` is an error even with `native_exit_code: 0`; its partial result
  is retained.

Unexpected failures use `internal_error` and still clean up the owned process group. If the
result was already written, Prat reports the failure only on stderr to keep stdout to one object.

## Previews and management commands

`--dry-run --json` returns a preview containing `dry_run`, resolved identity, argv, cwd, timeout,
and stdin byte count, rather than a run status. Active schemas add the original `schema` path and
`schema_transport`: `inline` when the schema's JSON is passed as an argument, `file` when the agent
reads a temporary prepared copy. The text preview says `inline JSON` or `temporary prepared file`.
Prepared temporary paths are labeled placeholders, not reusable filenames. Prompts passed through argv appear in the preview.

Management commands return separate versioned inventory or config objects. Their errors still
contain `status`, `exit_code`, and `error`.

`prat list --json` returns `{"schema_version": 1, "profiles": [...], "workflows": [...],
"templates": [...], "agents": [...]}`; `prat list SECTION --json` returns only that section's key.
Each array holds the records described below, sorted by name, except `agents`, which follows
catalog order. An agent record has `name`, `label`, `aliases`, `command`, `capabilities` (including
`fast`) and the boolean `installed`: whether the configured executable is found on `PATH`, the same
lookup `prat doctor` does. An unknown SECTION is `invalid_arguments`.
`prat completion` prints shell code and rejects `--json` with `invalid_arguments`.
`prat --version --json` returns `{"schema_version": 1, "version": "1.1.0"}`. A line with neither a
selector nor a management command, such as `prat --json`, returns the standard error object with
`invalid_arguments`.

`prat trust --json` and `prat trust --revoke --json` return `path` and the boolean `trusted`.
`prat trust --list --json` returns `directories`, an array of `{"path", "exists"}` records sorted by
path.

A `profiles` record has `name`, `agent`, `extends` (the parent profile or null), `source` (the
defining file's absolute path), `options` (every resolved option), `prompt` and `prompt_source`
(the file that defines the prompt), both null without a profile `prompt`, and `parameters`, the
prompt's parameter names in order.

A `templates` record contains `name`, the unexpanded `prompt`, the defining file's absolute `source` path, and
`parameters`, the prompt parameter names in order of first appearance (empty when there are none).
An empty inventory returns an empty array; replacement warnings remain on stderr.

Doctor records include `available`, plus nullable `version` and `version_error` fields. Without
`doctor --versions`, both version fields stay null and no configured command is launched.
Ordinary version-probe failures leave doctor at exit 0. An interrupted probe adds interrupted
management status and exits with `128 + signal`.

## Help pages

`--json` with any help form prints the page as one object and exits 0. `prat help X --json`,
`prat X --help --json` and `prat --json help X` print identical bytes for the same topic. An
unknown topic returns the standard error object with `invalid_arguments` (exit 2).

A selector page, such as `prat help weekly --json`, has:

- `kind`: `agent`, `profile` or `workflow`; `name`: the selector as typed, so `cc` stays `cc`.
- `entry`: that selector's record from `prat list --json`; for an alias, the agent record.
- `settings`: the SETTING, VALUE, ORIGIN rows of the text page as `{"name", "value", "origin"}`.
  `value` is the JSON value (`timeout` stays `600.0`, a list stays a list) and `origin` is the
  text page's string, such as `profiles.base`, `defaults (local)`, `agents.claude` or `built-in
  default`. An agent lists its resolved defaults the same way. A workflow has `null`.
- `run_options`: one record per line of the text page's run options, in the same order: every run
  option for an agent or profile, the workflow options for a workflow.
- `usage`: the usage line without the `usage:` prefix.

A run option record is:

```json
{
  "flags": ["--model"],
  "metavar": "MODEL",
  "setting": "model",
  "supported": true,
  "description": "Override the profile model."
}
```

`flags` keeps the text order, such as `["-f", "--file"]` or `["--fast", "--no-fast"]`. `metavar`
is null for a switch. `setting` is the profile setting the option overrides, or null. `supported`
is false when the selector's agent lacks the capability, so a run with that option would be
rejected; it is always true for a workflow and on the top-level page.

`prat help --json` returns `kind: "prat"`, `usage`, `commands` (`{"name", "description"}` for each
management command, in help order) and `run_options` for every run option.

A management command page, such as `prat help list --json`, `prat list --help --json` or `prat help
config init --json`, returns `kind: "command"`, `name` (`list`, `config init`), `description`,
`usage`, `options` and `arguments`. An option is `{"flags", "metavar", "description"}`. An argument
is `{"name", "choices", "description"}`: `choices` lists the accepted values, such as the `prat
list` sections or the `prat completion` shells, and is null when any value is accepted. `prat help
config --json` has one `COMMAND` argument whose choices are the config commands and whose
`description` is null; each config command has its own page.

Descriptions are the strings the text help prints. Read fields by name: help text can be reworded.
