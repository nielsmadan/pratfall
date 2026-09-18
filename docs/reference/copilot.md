# Copilot

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Published Copilot 1.0.83 package.

## Native contract

A direct CLI report for v1.0.54 confirms JSONL `assistant.message` with final text at
`data.content`: [CLI issue 3544](https://github.com/github/copilot-cli/issues/3544).
The [official SDK event types](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
define `assistant.message` data with content/messageId and optional model/chunkCount/chunkIndex,
`session.error` data with errorType/message, `session.idle` data with optional `aborted`, and
`session.shutdown` data with shutdownType/errorReason/currentModel/modelMetrics. SDK schemas
are primary source for the events, but CLI completion emission still needs confirmation.
Root cached the SDK types in `.cache/research/copilot-session-events.ts`.
Model shutdown usage has inputTokens/outputTokens/cacheReadTokens/cacheWriteTokens.
Do not expose nested subagent text: event agentId and data.parentToolCallId identify it.

The published 1.0.83 CLI source confirms that its own terminal event is `type: result` with an
`exitCode` and non-token usage summary. It filters SDK `session.idle`, `session.shutdown`, and
`assistant.usage`, so those events are not CLI completion evidence. Prat requires the CLI result,
deduplicates deltas and full messages by message ID, and excludes messages with subagent markers.
A `session.error` of type `model_call` is recoverable; other session errors and the native terminal
policy warnings are failures. The terminal summary has credits, durations, and code-change counts
but no tokens, so normalized usage is null. The prompt uses documented `--prompt=VALUE` and remains
subject to the operating system's argv-size limit.
Root completed `assistant.message.data.model` identifies an observed model. Prat keeps distinct IDs
in order and excludes model fields on deltas, subagent messages, and unrelated request/config data.
The terminal credits metric is not USD and is not exposed as `cost_usd`.

Copilot's tool and URL allow/deny lists, tool visibility lists, and secret environment-variable
list accept zero or more values per occurrence. A bare `--available-tools` disables all visible
tools. `--bash-env` and `--no-bash-env` persist native configuration, so Prat does not pass them
through; a trusted executable wrapper remains the escape hatch for intentionally persistent setup.

## Sources

- [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/copilot.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Extra directories (verified 2026-09-18)

The [programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
defines repeated `--add-dir=DIRECTORY` entries in the allowed-paths list. Prat emits that exact
form; it does not add `--allow-all-paths` or tool approvals.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.
