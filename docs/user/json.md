# JSON output

Run mode `--json` writes exactly one versioned result object to stdout. Diagnostics remain on
stderr. Config and argument failures also use this shape when `--json` is unambiguous.

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
  "error": null
}
```

`model` is the resolved model requested from the native CLI. It is null when delegated to native
defaults; it is not a verified record of every model a native tool might use internally. Usage
contains nullable `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
`output_tokens`, and `reasoning_output_tokens` fields when a native protocol reports them.
Unknown usage stays null.

Token fields retain each native protocol's counter semantics. For some agents, native input totals
already include cached tokens, so do not blindly add `input_tokens` and `cached_input_tokens` to
estimate billing.

Statuses are `success`, `error`, `timeout`, and `interrupted`. Invalid input exits 2, missing and
non-executable commands exit 127 and 126, timeouts exit 124, and signals exit `128 + signal`.
Provider and protocol failures exit 1 unless a native nonzero exit has precedence.

`--dry-run --json` emits a preview with `dry_run`, resolved identity, argv, cwd, timeout, and stdin
byte count rather than a run status. Prompts carried through argv appear in that preview. Management
commands use separate versioned inventory or config objects; their errors still contain `status`,
`exit_code`, and `error`.
