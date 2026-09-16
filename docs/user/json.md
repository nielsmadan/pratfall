# JSON output

Use `--json` to write one versioned result object to stdout. Diagnostics stay on stderr;
`--progress` adds bounded live activity there without changing the JSON fields.
Read fields by name: their order can differ on config or argument errors. Those errors use the
same fields when `--json` is unambiguous.

- [Run results](#run-results)
- [Models, usage, and cost](#models-usage-and-cost)
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
  "cost_usd": null
}
```

`status` is `success`, `error`, `timeout`, or `interrupted`. Success has `exit_code: 0` and
`error: null`. For text adapters, `output` contains complete native stdout with trailing CR/LF
removed, which may include banners and progress text.

## Models, usage, and cost

| Field | Meaning |
| --- | --- |
| `model` | Requested model after resolving settings; null when using native defaults. |
| `reported_models` | List of distinct native model identifiers in observed order, or null if unknown. May include supporting calls, not just models that generated final text. |
| `cost_usd` | Native run cost in US dollars: a finite, nonnegative number, or null if unknown. This is not a subscription-billing guarantee. |
| `usage` | Native token counters, or null if unknown. Individual counters can also be null. |

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
| `interrupted` | `128 + signal` | Prat received SIGINT or SIGTERM. |
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
and stdin byte count, rather than a run status. Prompts passed through argv appear in the preview.

Management commands return separate versioned inventory or config objects. Their errors still
contain `status`, `exit_code`, and `error`. Agent capability records include `fast`.

Doctor records include `available`, plus nullable `version` and `version_error` fields. Without
`doctor --versions`, both version fields stay null and no configured command is launched.
Ordinary version-probe failures leave doctor at exit 0. An interrupted probe adds interrupted
management status and exits with `128 + signal`.
