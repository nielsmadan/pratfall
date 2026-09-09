# Agent interface evidence

Verified 2026-09-09 for the initial implementation. This is a working evidence register;
adapter tasks replace uncertainties with verified contracts. Installed help was read without
model inference. Native CLI releases can change these interfaces.

| Agent | Native invocation | Model | Effort | Native limits | Evidence |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `claude -p --output-format json` | `--model` | `--effort` | `--max-budget-usd`, `--max-turns` | Installed 2.1.266, [CLI reference](https://code.claude.com/docs/en/cli-reference), and [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage) |
| Codex | `codex exec --json` | `--model` | `-c model_reasoning_effort="LEVEL"` | No verified total-token/spend cap | Installed 0.153.4, [noninteractive docs](https://developers.openai.com/codex/noninteractive), [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs) |
| Gemini | `gemini --output-format json --prompt=PROMPT` | `--model` | Unsupported | None verified | [Headless reference](https://geminicli.com/docs/cli/headless) and cached formatter source/tests |
| Antigravity | `agy --input-format stream-json --output-format stream-json` with one stdin user event | `--model` | `--effort low\|medium\|high` | `--print-timeout` belongs to native print mode and is reserved | Installed 1.1.11 and [headless docs](https://antigravity.google/docs/cli/headless) |
| Copilot | `copilot --output-format=json --prompt=PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | `--max-ai-credits`, soft per-response cap | [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) and published 1.0.83 package source |
| Kiro | `kiro-cli chat --no-interactive PROMPT` | No verified override | `--effort` | Unverified | [CLI reference](https://kiro.dev/docs/reference/cli-commands/), [headless](https://kiro.dev/docs/cli/headless/) |
| Cursor | `agent --print --output-format json agent -- PROMPT` | `--model` | Unsupported | None verified | [Parameters](https://cursor.com/docs/cli/reference/parameters), [output format](https://cursor.com/docs/cli/reference/output-format), and published 2026.09.08 package source |
| OpenClaw | `openclaw agent exec --json --message-file -` | `--model provider/model` | `--thinking` | Native timeout in seconds | [Agent exec](https://docs.openclaw.ai/cli/agent) |
| Hermes | `hermes chat --oneshot --quiet --query-file -` | `--model`, `--provider` | Top-level reasoning dispatch needs verification | `--max-turns` | [CLI docs](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [source](https://github.com/NousResearch/hermes-agent/blob/main/cli.py) |
| OpenCode | `opencode run --format json` with stdin prompt | `--model provider/model` | `--variant` (provider-specific string) | None verified | Installed 1.18.29 help and cached `run.ts`/session source |

## Verified output contracts

### Claude Code

A live read-only Haiku plan consultation with `--output-format json` returned one JSON object
with `type: result`, `subtype: success`, `is_error: false`, `result: STRING`, native usage and
modelUsage, duration fields, and `terminal_reason: completed`. Successful exit was 0.
Current help lists effort low/medium/high/xhigh/max; docs additionally describe model-dependent
ultracode. Model-name catalogs should not be frozen in pratfall.

The Agent SDK's `SDKResultMessage` is a union. Only the `success` arm has `result: string`.
The `error_max_turns`, `error_during_execution`, `error_max_budget_usd`, and
`error_max_structured_output_retries` arms instead have `errors: string[]`; both arms carry
native usage. Prat preserves those diagnostic strings in the normalized provider error and
retains usage. The error subtype is a semantic failure even when the native process exits 0.

### Codex

`codex-rs/exec/src/exec_events.rs` defines tagged JSONL events: `thread.started`, `turn.started`,
`turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`, and `error`.
`item.completed.item` carries `id`, `type`, and type-specific fields. Final assistant messages
use `type: agent_message` and `text`; reasoning and command/tool contents are separate item types.
`turn.completed.usage` contains `input_tokens`, `cached_input_tokens`, `output_tokens`, with
newer optional cache-write/reasoning fields. `turn.failed.error.message` and top-level
`error.message` are failures. A terminal success event is required; EOF alone is not success.

### Gemini

JSON mode returns `response` (final answer), `stats`, and optional `error`. Streaming mode also
exists but is unnecessary for final-only output. Native exits: 0 success, 1 API/general error,
42 input error, 53 turn limit. Local execution failed on a nono grant to trustedFolders.json;
no settings were read or changed to work around it.

Prat uses the documented `--prompt=VALUE` form so leading dashes stay prompt data. The final JSON
object is completion evidence; an `error` object is a provider failure even if native exit is zero.
Usage sums `stats.models[*].tokens` once per model: `input` is fresh input, `cached` is cache-read
input, `candidates` is output, and `thoughts` is reasoning output. Per-role token views repeat these
counts and are ignored. An empty model map reports nullable usage. The prompt travels in argv, so
the operating system may reject a large prompt before reaching Prat's 1 MiB bound.

### Antigravity

The headless docs' single-JSON example has `status: SUCCESS`, `response: STRING`,
`conversation_id`, `duration_seconds`, `num_turns`, and `usage` with input/output/thinking,
cache-read and total tokens. Failures have `status: ERROR`, an `error` string and nonzero exit.
The native default wait is five minutes; `--print-timeout` accepts duration strings such as
`15m`. JSON mode is sufficient; its JSONL mode instead uses `event` with nested `result`.

Prat uses the documented stdin stream mode, writes one `user` event, then closes stdin. The stream
must begin with `init` and contain exactly one terminal `result`. Only that result's response is
returned; step text, tools, and subagent metadata are omitted. Its cumulative single-turn usage
maps input, cache-read, output, and thinking counts directly. `SUCCESS` completes; failure states
remain provider failures and `WAITING` or `RUNNING` cannot serve as terminal evidence.

### OpenCode

[run.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts)
emits JSONL objects with `type`, timestamp, sessionID and a payload. Completed text parts are
`type: text`, `part: {type: text, id, text, time: {end, ...}, ...}`. `step_finish` wraps a
`step-finish` part with reason and token statistics. A tool-call step is not final completion.
Native `session.error` is surfaced as `type: error`, `error: {name,data: {message,...},...}`.
`MessageOutputLengthError` is the verified exception with empty `data`; the CLI reports its native
name when no message exists. Reasoning and tool events are separate. Native CLI waits for its own
session to become idle without emitting that idle event, so decoder completion must use the
verified step-finish reason.
Root cached source in `.cache/research/opencode-run.ts`.

The run source reads stdin whenever no positional message is supplied, so Prat uses stdin for the
prompt. Completed text parts are deduplicated by part ID. The latest token snapshot for each step ID
replaces earlier copies, and distinct steps are summed. OpenCode already separates fresh input from
cache reads/writes and text output from reasoning; Prat preserves that split. `tool-calls` continues
the run, `stop` is completion, and `length`, `content-filter`, `error`, or `unknown` finishes are
reported as incomplete provider failures.

### Kiro

Docs advertise `--output-format stream-json` on V2/V3, but omit event schemas. V3 documentation
also says classic `kiro-cli chat` does not support V3, so do not silently force an engine.
Native exit codes include 0 success, 1 failure, 3 mandatory MCP startup failure.
Do not invent `--model`, confuse model-list `--format json` with chat format, or build a
structured decoder from convenient guessed fixtures. Find authentic evidence first.

### Copilot

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

Copilot's tool and URL allow/deny lists, tool visibility lists, and secret environment-variable
list accept zero or more values per occurrence. A bare `--available-tools` disables all visible
tools. `--bash-env` and `--no-bash-env` persist native configuration, so Prat does not pass them
through; a trusted executable wrapper remains the escape hatch for intentionally persistent setup.

### Cursor

JSON print mode succeeds with one `type: result`, `subtype: success`, `is_error: false` object and a
string `result`. The docs state that failures exit nonzero and need not emit well-formed JSON, so
native exit status takes precedence over decoder failure. Published package source confirms
Commander's end-of-options handling. Prat supplies global print/model options first, then the
explicit `agent` command and `--` before the prompt; command-like text such as `login` and flags such
as `--force` remain data. No token usage is documented, and the argv prompt inherits OS size limits.

### OpenClaw

`agent exec` is embedded and explicitly avoids connecting to a gateway. It consumes the stdin
prompt through `--message-file -`. JSON has `ok: boolean`, `status: ok|error|timeout`, `final`,
`payloads`, optional `usage: {input,output,total}`, optional `error: {message,kind}`, nullable
model/provider, and sessionId. Native exits are 0 success, 1 result/cleanup error, 2 timeout.
Map native timeout to prat's 124. Temporary native run state normally cleans up automatically.

### Hermes

Do not use `hermes -z` as the default. [oneshot.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/oneshot.py)
unconditionally enables `HERMES_YOLO_MODE` and `HERMES_ACCEPT_HOOKS`, and can exit 0 after a
nonempty failed result. The quiet chat path preserves the separate explicit `--yolo` control,
prints final response text on stdout and diagnostics/session id on stderr, checks `result.failed`
for exit 1, and exits 130 on interruption. Quiet output has no usage JSON, so usage is null.

## Remaining factual verification

- Kiro authentic JSONL schema or a documented native text limitation.
- Hermes top-level `--reasoning` dispatch and any additional supported limits.
- Most agents are absent locally; fixture coverage must be distinguished from live verification.

## Source acquisition notes for implementers

Use `gh` to read GitHub sources. Root cached Codex source under
`.cache/research/codex-exec-events.rs` for this checkout. `jina-fetch` caches docs and prints
their exact paths; extract short verbatim anchors from those cached files when schema text
matters. Never use a summarizer's reconstructed command as sole evidence for exact argv.
