# Codex

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Codex 0.153.4.

## Native contract

`codex-rs/exec/src/exec_events.rs` defines tagged JSONL events: `thread.started`, `turn.started`,
`turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`, and `error`.
`item.completed.item` carries `id`, `type`, and type-specific fields. Final assistant messages
use `type: agent_message` and `text`; reasoning and command/tool contents are separate item types.
`turn.completed.usage` contains `input_tokens`, `cached_input_tokens`, `output_tokens`, with
newer optional cache-write/reasoning fields. `turn.failed.error.message` and top-level
`error.message` are failures. A terminal success event is required; EOF alone is not success.
Codex's supported events expose no verified model or cost accounting. If EOF or Prat's outer
timeout arrives after a completed assistant message but before `turn.completed`, Prat retains the
latest message after any earlier completed-turn answers and still reports failure.

## Recorded compatibility observation

On 2026-09-09, Codex 0.153.4 with `gpt-5.6-luna`, low effort and ephemeral sessions passed three
installed-wheel checks: exact text output, a multiline profile/stdin JSON response with usage,
and an exact edit to the sole file in a disposable directory. Response checks used read-only
sandboxing and 90-second deadlines; the edit used workspace-write and a 120-second deadline.
The tested wheel's SHA256 was
`1aca00c48236ecc7ffcb763c41c2518b0b1c506f0a6f7237c4969b2b9536b174`.

The edit emitted two nested-sandbox `apply_patch` failures before succeeding. Native events were
not retained, so the recovery mechanism is unknown. Later runtime changes used offline fixture
verification; these observations do not establish compatibility of the current package or newer
native versions. Repeat live checks only when authorized, recording native version, artifact hash,
argv/input, working directory, stdout/stderr, exit status and file results.

## Sources

- [noninteractive docs](https://developers.openai.com/codex/noninteractive)
- [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/codex.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
