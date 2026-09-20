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

## Session id (verified 2026-09-19)

The terminal `result` event carries `sessionId`, which Prat already required to be a string.
It is now retained in the `session` slot and reported as `native_session_id`, charged against
the retained-state budget like the message identifiers.

The [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
documents `--session-id ID` as an exact session or task id: "If the ID matches an existing
session or task, that session or task is resumed. If nothing matches, a new session is created
only when the value is a valid UUID." A freshly generated UUID therefore names a new session,
so Prat grants the `session_id` capability and forwards `--session-id=ID`. The dual mode is
real and the caller owns it: reusing an id resumes. The same reference warns against combining
it with `--resume`, `--continue` or `--connect`; all three stay reserved.

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

## Tool availability (verified 2026-09-18)

The [programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
says `--available-tools` will “Restrict the model to only the tools you list” and describes quoted
comma-separated lists. `--excluded-tools` removes tools from model visibility. These differ from
permission approvals (`--allow-tool`) and denials (`--deny-tool`). Prat emits
`--available-tools=LIST` and `--excluded-tools=LIST`, rejecting commas and surrounding native
whitespace inside entries. Names remain Copilot's native vocabulary.

The pinned 1.0.83 package's variadic zero-value contract above establishes bare
`--available-tools` as an empty selection. Prat uses it for `tools=[]`; following arguments are
flags, so the empty selection cannot consume prompt text. Equivalent native flags conflict with
active public lists. An active public allowlist also conflicts with native MCP enable/addition
flags, preventing ambiguous tool-set expansion. Explicit native permission denials remain accepted.

No approval flags are injected. Verification used primary documentation/package evidence and
fake argv tests, not authenticated vendor execution.

## Native agent selection (verified 2026-09-18)

The [programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference#using-custom-agents)
states: “You can delegate work to a specialized agent by using the `--agent` option.” Its example
selects an existing `code-review` custom agent. Prat maps `native_agent` to `--agent=NAME` and
adds no permission approval. Names are literal and native configuration remains authoritative.
Native-only `--agent` stays accepted; an active public selection conflicts with it.
Verification used primary documentation and fake executable argv tests, not authenticated execution.

## Native attachments (verified 2026-09-18)

The published [1.0.83 package](https://www.npmjs.com/package/@github/copilot/v/1.0.83) declares
`--attachment <path>` with an array accumulator and describes “image or native document” inputs.
Its `NCn` preparation function checks `isFile()`, calls the native attachable-path helper and
constructs `{type:"file",path:o,displayName:...}`. Prat emits repeated `--attachment=PATH`,
preserves order and passes canonical readable regular-file paths. Active public attachments
conflict with native `--attachment`; native-only use stays accepted. Native document/image types,
content validity, model support and limits remain authoritative.

Evidence is static primary package inspection plus fake argv tests, not authenticated execution.
