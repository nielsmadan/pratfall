# Reasonix 2.37.0

**Evidence recorded:** 2026-09-11, refreshed against 2.37.0 on 2026-10-13.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The npm distribution now installs the maintained 2.x terminal CLI. Version 2.37.0 keeps the
one-shot invocation and core JSON result used by the adapter. Source moved to
`internal/frontend/cli/`; this audit inspected the release sources, not a native execution.
Install `npm install -g reasonix@2.37.0`, then run `reasonix setup` separately.

The pinned [run parser](https://github.com/esengine/DeepSeek-Reasonix/blob/v2.37.0/internal/frontend/cli/cli.go)
establishes `reasonix run --output-format json` with plain stdin, which the native reader trims.
`--model` selects a configured provider name, and `--effort` passes a session effort override
whose accepted levels depend on that provider. Accepted native options are `--max-steps` and
`--permission-mode` (one value each), plus `--show-thinking` and `--fail-on-unverified` (zero). Max steps counts native
rounds and is not normalized to max_turns. Prompt/print/output/events, model/effort, config/dir,
resume/continue/copy/takeover, serve and administrative selectors are reserved or rejected.
Version probing uses `--version`. The [versioned CLI guide](https://github.com/esengine/DeepSeek-Reasonix/blob/v2.37.0/docs/CLI.md)
defaults to `auto` only for a trusted workspace with working write-confining sandbox support;
otherwise it uses `ask`. Headless ask mode refuses writes. Prat adds no approval override and
configures no providers. Native `--permission-mode plan` requires an interactive
session and exits 2 from `run`; explicit passthrough preserves that native failure.

The [whole JSON result](https://github.com/esengine/DeepSeek-Reasonix/blob/v2.37.0/internal/frontend/cli/run_output.go)
and [completion classification](https://github.com/esengine/DeepSeek-Reasonix/blob/v2.37.0/internal/frontend/cli/run_completion.go)
require type result, a result string and a known subtype/is_error combination. Only success/false
completes successfully. Version 2.37.0 emits success/false or error_during_execution/true.
The decoder also retains older incomplete_read, recovery_paused and completion_uncertain
handling for 1.x compatibility. Prat retains result text and usage on failures.

Current native readiness failures can still be success/false with exit zero. This is completion,
not a guarantee that the requested task was achieved. Explicit `--fail-on-unverified` converts
unmet readiness to exit 3; Prat never injects it. Untrusted-folder refusal exits 4, or 3 with that
flag, even when its JSON envelope says success. Native nonzero status remains authoritative;
see [run verdicts](https://github.com/esengine/DeepSeek-Reasonix/blob/v2.37.0/internal/frontend/cli/run_verdict.go).

Input/output and cache_read_input_tokens are native counts. Cache_creation_input_tokens is filled
from CacheMissTokens, so cache-write usage remains unknown. The estimated bit is telemetry;
Prat does not calculate replacement counts. Total_cost_usd aliases total_cost in its reported
currency, so cost_usd stays null. Reported models are unavailable. Whole-document bounds apply;
excessive nesting/numeric width, nonfinite numbers and invalid Unicode produce normalized errors.
These contracts were statically checked on 2026-09-11 before accepting fake executable fixtures.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/reasonix.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
