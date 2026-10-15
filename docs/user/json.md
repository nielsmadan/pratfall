# JSON output

Use `--json` to write one versioned result object to stdout. Diagnostics stay on stderr;
`--progress` adds bounded live activity there without changing the JSON fields.
Read fields by name: their order can differ on config or argument errors. Those errors use the
same fields when `--json` is unambiguous.

- [Run results](#run-results)
- [Models, usage, and cost](#models-usage-and-cost)
- [Workflow results](#workflow-results)
- [Errors and partial results](#errors-and-partial-results)
- [Previews and management commands](#previews-and-management-commands)

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

Amp, OpenHands, and Vibe have no generic model override.

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
no step reports usage. `cost_usd` is likewise summed. `duration_ms` is the workflow's wall time.

Every declared step appears in `steps`, in declared order. A step that ran carries its
[run result](#run-results) fields after `step`; a step that never started is
`{"step": NAME, "status": "skipped"}`. A step Prat could not launch or whose child returned an
unusable result carries a synthesized result with `output: ""`, null `usage` and `cost_usd`, and
an `error`. A workflow `status` and `exit_code` are the first failed step's; on interruption they
are `interrupted` and `128 + signal`, with error code `interrupted`. An error that stops the
workflow before any step launches, such as a bad parameter, reports `steps: []` and
`duration_ms: 0`. `error.message` names the failing step as `step 'NAME': MESSAGE`.

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

`prat workflows --json` returns `schema_version: 1` and a `workflows` array sorted by name. Each
record has `name`, `parallel`, `parameters`, the defining file's absolute `source`, and `steps`,
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
| `native_exit` | the native status | The native CLI exited nonzero without another failure. |
| `protocol_error` | 1 | Native output violated the agent's documented protocol. |
| `provider_error` | 1 | The native CLI reported a provider or model failure. |
| `output_encoding` | 1 | Native output was not valid UTF-8 or not representable text. |
| `output_io_error` | 1 | Prat could not read agent output or write diagnostics. |
| `process_io_error` | 1 | Process setup or I/O failed before a result existed. |
| `internal_error` | 1 | Prat hit an unexpected failure and normalized it instead of aborting. |
| `stdout_limit_exceeded` | 1 | Native stdout or retained decoder state exceeded its budget. |
| `stderr_limit_exceeded` | 1 | Native stderr exceeded its budget. |
| `unsupported_platform` | 1 | Process execution requires POSIX. |

The three signal- and status-derived exit codes vary per run; all others are fixed. Provider
and protocol failures exit 1 unless a native nonzero exit takes precedence.

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
`schema_transport`; prepared temporary paths are labeled placeholders, not reusable filenames. Prompts passed through argv appear in the preview.

Management commands return separate versioned inventory or config objects. Their errors still
contain `status`, `exit_code`, and `error`. Agent capability records include `fast`.
`prat completion` prints shell code and rejects `--json` with `invalid_arguments`.

`prat profiles --json` records add `prompt` and `prompt_source` (the defining file's absolute
path), both null without a profile `prompt`, and `parameters`, the prompt's parameter names in
order. In text listings, `prat profiles` appends ` prompt="..."` and ` parameters=a,b`, and
`prat templates` appends ` parameters=a,b`.

`prat templates --json` returns `schema_version: 1` and a `templates` array sorted by name. Each
record contains `name`, the unexpanded `prompt`, the defining file's absolute `source` path, and
`parameters`, the prompt parameter names in order of first appearance (empty when there are none).
An empty inventory returns an empty array; replacement warnings remain on stderr.

Doctor records include `available`, plus nullable `version` and `version_error` fields. Without
`doctor --versions`, both version fields stay null and no configured command is launched.
Ordinary version-probe failures leave doctor at exit 0. An interrupted probe adds interrupted
management status and exits with `128 + signal`.
