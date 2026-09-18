# Qwen Code 0.23.3

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The pinned [headless guide](https://github.com/QwenLM/qwen-code/blob/v0.23.3/docs/users/features/headless.md)
and [option declarations](https://github.com/QwenLM/qwen-code/blob/v0.23.3/packages/cli/src/config/top-level-options.ts)
establish `qwen --output-format stream-json` with plain text stdin. Prat maps model to `--model`
and max_turns to `--max-session-turns`; effort, fast and spend budgets are unsupported.
Accepted native flags are `--debug`/`-d` (zero values), `--approval-mode`, `--system-prompt` and
`--append-system-prompt` (one value each). Prompt/input/output, native model/turn/budget controls,
config/cwd, continuation/session, ACP/serve and fallback selectors are reserved or rejected.
The version diagnostic uses `--version`. Existing native permissions apply; unresolved interactive
approval requests are denied in headless mode. No approval bypass is added.

The [message types](https://github.com/QwenLM/qwen-code/blob/v0.23.3/packages/cli/src/nonInteractive/types.ts)
and [result builder](https://github.com/QwenLM/qwen-code/blob/v0.23.3/packages/cli/src/nonInteractive/io/BaseJsonOutputAdapter.ts)
define root assistant text/model fields under `message`, with `parent_tool_use_id: null`.
Child assistant records, thinking and tools cannot replace root text or reported models. Root
model identifiers are retained in observed order. Final result usage is authoritative: input,
output and optional cache-read token counts are normalized without adding message snapshots.
Cache writes and USD cost remain unknown.

A success requires result subtype `success`, `is_error: false` and a result string. Failures use
`error_during_execution` or `error_max_turns`, `is_error: true` and an error object with a message.
The [stream emitter](https://github.com/QwenLM/qwen-code/blob/v0.23.3/packages/cli/src/nonInteractive/io/StreamJsonOutputAdapter.ts)
can emit intermediate subagent error results without a parent discriminator. The
[root run loop](https://github.com/QwenLM/qwen-code/blob/v0.23.3/packages/cli/src/nonInteractiveCli.ts)
waits for background task completion before emitting its final result (lines 3077–3184), and
emits a result on its failure path (3249–3305). The last valid result therefore governs; repeated
results are accepted, malformed records remain errors, and assistant text alone cannot complete
successfully. Only known system, user and stream-event records are ignored. JSONL bounds apply.
These contracts were statically checked on 2026-09-11 before accepting fake executable fixtures.

## Implementation

An unpaired surrogate in a reported-model identifier produces `protocol_error` through model
validation. This differs from an unencodable answer's `output_encoding` error and is pinned by
[the retention contract tests](../../tests/test_retention_contract.py).

Model deduplication in the adapter's [`_assistant`](../../src/pratfall/adapters/qwen.py) handler
uses list membership and scales quadratically. A synthetic 2026-09-11 review measured about three
seconds for 16,380 distinct 500-byte root model identifiers, within the byte/record limits. Ordinary native streams
report few models; this performance case remains deferred. The runner checks deadlines between reads.

- [Pratfall adapter](../../src/pratfall/adapters/qwen.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Extra directories (verified 2026-09-18)

The [v0.24.0 option declarations](https://github.com/QwenLM/qwen-code/blob/v0.24.0/packages/cli/src/config/top-level-options.ts)
define `--include-directories` with alias `--add-dir`; the same declaration exists in v0.23.3.
The [parser](https://github.com/QwenLM/qwen-code/blob/v0.24.0/packages/cli/src/config/config.ts)
splits comma-separated values and trims each segment. Prat repeats `--include-directories PATH`
and rejects commas or surrounding whitespace in resolved paths. Both native aliases conflict
with a nonempty public directory list.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Appended instructions (verified 2026-09-18)

The [v0.24.0 option declarations](https://github.com/QwenLM/qwen-code/blob/v0.24.0/packages/cli/src/config/top-level-options.ts)
describe `--append-system-prompt`: “Append instructions to the main session system prompt for this
run. Can be combined with --system-prompt.” The same append option was supported in the v0.23.3
baseline. Prat passes `--append-system-prompt=TEXT`, retaining leading dashes as value data.
Native-only append text remains supported; either active public instruction form conflicts with it.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.
