# Reasonix 1.38.5

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The pinned [run parser](https://github.com/esengine/DeepSeek-Reasonix/blob/v1.38.5/internal/cli/cli.go)
establishes `reasonix run --output-format json` with plain stdin, which the native reader trims.
`--model` selects a configured provider name, and `--effort` passes a session effort override
whose accepted levels depend on that provider. Accepted native options are `--max-steps` and
`--permission-mode` (one value each), plus `--show-thinking` (zero). Max steps counts native
rounds and is not normalized to max_turns. Prompt/print/output/events, model/effort, config/dir,
resume/continue/copy/takeover, serve and administrative selectors are reserved or rejected.
Version probing uses `--version`. Default headless ask permissions fail closed; Prat does not
add autoapproval or configure providers. Native `--permission-mode plan` requires an interactive
session and exits 2 from `run`; explicit passthrough preserves that native failure.

The [whole JSON result](https://github.com/esengine/DeepSeek-Reasonix/blob/v1.38.5/internal/cli/run_output.go)
and [completion classification](https://github.com/esengine/DeepSeek-Reasonix/blob/v1.38.5/internal/cli/run_completion.go)
require type result, a result string and a known subtype/is_error combination. Only success/false
completes successfully. Incomplete_read, recovery_paused and completion_uncertain have false
is_error but remain incomplete; recovery_paused can exit zero. Error_during_execution has true
is_error. Prat retains result text and usage while normalizing these failures.

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
