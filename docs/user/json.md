# JSON output

Run mode `--json` writes exactly one versioned result object to stdout. Diagnostics remain on
stderr. Config and argument failures carry the same fields when `--json` is unambiguous, though they
appear in a different order; JSON objects are unordered, so read by key rather than by position.
`--progress` adds bounded live activity on stderr without adding extra stdout objects or fields.

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
  "usage": null,
  "error": null,
  "reported_models": null,
  "cost_usd": null
}
```

`model` is the resolved model requested from the native CLI. It is null when delegated to native
defaults. `reported_models` is a nullable list of distinct native model identifiers in observed
order; it can include supporting calls and does not say that every listed model generated final
text. `cost_usd` is a nullable finite nonnegative cost reported in US dollars by the native CLI.
It is native run accounting, not a subscription-billing guarantee. Unknown model or cost data stays
null rather than falling back to the requested model or zero. Copilot credits are not converted to
USD.

Reasonix's requested `model` selects a configured provider name. Amp, OpenHands and Vibe have no
generic model override. For text adapters, `output` contains complete native stdout with terminal
CR/LF removed; banners and progress can be part of that text.

Usage contains nullable `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
`output_tokens`, and `reasoning_output_tokens` fields when a native protocol reports them. Unknown
usage stays null.

Token fields retain each native protocol's counter semantics. For some agents, native input totals
already include cached tokens, so do not blindly add `input_tokens` and `cached_input_tokens` to
estimate billing.

Statuses are `success`, `error`, `timeout`, and `interrupted`. Invalid input exits 2, missing and
non-executable commands exit 127 and 126, timeouts exit 124, and signals exit `128 + signal`.
Provider and protocol failures exit 1 unless a native nonzero exit has precedence.

## Error codes and exit codes

`error` is null on success, and otherwise an object with a stable `code` and a human-readable
`message`. The vocabulary below is the complete stable set; `exit_code` is determined by the code.

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

A rejected option follows the source that supplied it: a command-line override reports
`invalid_arguments`, while a `defaults` or profile value reports `invalid_config`. A rejected
native argument stays `invalid_arguments` from either source.

The three signal- and status-derived rows are computed per run; every other code has the fixed
exit code above. `internal_error` is the last-resort code: an unexpected failure anywhere in a run
still produces one result object and still cleans up the owned process group. When the result was
already written to stdout, the failure is reported on stderr alone so that stdout keeps exactly one
object. A successful run reports `status` `success`, `exit_code` 0, and null `error`.
Successfully decoded output, usage, reported models, and cost remain in failed or timed-out results.
For Codex, a completed assistant message before an unfinished turn ends is returned as partial
output, while the missing `turn.completed` remains a protocol error.
Local output framing, state, encoding, and presentation failures also retain fields decoded before
the failure when they are safe to present.

`--dry-run --json` emits a preview with `dry_run`, resolved identity, argv, cwd, timeout, and stdin
byte count rather than a run status. Prompts carried through argv appear in that preview. Management
commands use separate versioned inventory or config objects; their errors still contain `status`,
`exit_code`, and `error`.

Agent capability records include `fast`. Doctor inventory records include nullable `version` and
`version_error` fields, separate from `available`. Without `doctor --versions`, both version fields
remain null and no configured command is launched. Ordinary probe failures leave doctor at exit 0.
An interrupted version probe adds interrupted management status and exits with `128 + signal`.

Qwen reports models only from root assistant messages and takes usage from the final native result.
Amp also uses only optional final result usage and reports no model identity or USD cost. Reasonix
reports native input/output/cache-read counts; its cache-creation field means cache misses and its
USD cost alias may have another currency, so cache-write counts and cost_usd stay null. Reasonix
recovery_paused is an error outcome even when native_exit_code is zero, with its partial result retained.
