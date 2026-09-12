# Agent interface evidence

Verified 2026-09-09 for the initial implementation. This is a working evidence register;
adapter tasks replace uncertainties with verified contracts. Installed help was read without
model inference. Native CLI releases can change these interfaces.

Fast mode and version probes were verified separately on 2026-09-10. Codex 0.153.4's configuration
schema accepts `service_tier` strings; Prat maps true to the invocation override
`-c service_tier="priority"` and false to `-c service_tier="default"`. Claude's fast-mode guide
documents noninteractive `--settings '{"fastMode": true}'`, and its CLI reference says inline
settings apply to that session; Prat passes the same single-key object with either boolean. Omission
adds no override. Native account and model restrictions still apply, and settings files are never
changed.

All ten catalog entries use a conventional `--version` diagnostic. The configured prefix is kept
literal. Prat treats the stripped stdout, or stderr when stdout is empty, as opaque strict UTF-8 and
does not infer authentication. A nonzero exit, empty selected output, invalid selected encoding,
timeout, or output overflow becomes a per-agent `version_error`. The default doctor path performs
discovery only.

Native accounting mappings were verified separately on 2026-09-10. Prat exposes only observed
model identifiers and native USD cost fields described below. Missing data remains null; requested
models, published prices, and non-USD credits are not used as substitutes.

| Agent | Native invocation | Model | Effort | Native limits | Evidence |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `claude -p --output-format json` | `--model` | `--effort` | `--max-budget-usd`, `--max-turns` | Installed 2.1.266, [CLI reference](https://code.claude.com/docs/en/cli-reference), and [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage) |
| Codex | `codex exec --json` | `--model` | `-c model_reasoning_effort="LEVEL"` | No verified total-token/spend cap | Installed 0.153.4, [noninteractive docs](https://developers.openai.com/codex/noninteractive), [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs) |
| Gemini | `gemini --output-format json --prompt=PROMPT` | `--model` | Unsupported | None verified | [Headless reference](https://geminicli.com/docs/cli/headless) and cached formatter source/tests |
| Antigravity | `agy --input-format stream-json --output-format stream-json` with one stdin user event | `--model` | `--effort low\|medium\|high` | `--print-timeout` belongs to native print mode and is reserved | Installed 1.1.11 and [headless docs](https://antigravity.google/docs/cli/headless) |
| Copilot | `copilot --output-format=json --prompt=PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | `--max-ai-credits`, soft per-response cap | [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) and published 1.0.83 package source |
| Kiro | `kiro-cli chat --no-interactive --wrap never -- PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | None verified | [CLI reference](https://kiro.dev/docs/reference/cli-commands/), [headless](https://kiro.dev/docs/cli/headless/), and statically inspected 2.21.2 package |
| Cursor | `agent --print --output-format json agent -- PROMPT` | `--model` | Unsupported | None verified | [Parameters](https://cursor.com/docs/cli/reference/parameters), [output format](https://cursor.com/docs/cli/reference/output-format), and published 2026.09.08 package source |
| OpenClaw | `openclaw agent exec --json --message-file -` | `--model provider/model` | `--thinking` | Native timeout in seconds | [Agent exec](https://docs.openclaw.ai/cli/agent) |
| Hermes | `hermes chat --oneshot --quiet --query-file -` | `--model`, `--provider` | `--reasoning none\|minimal\|low\|medium\|high\|xhigh\|max\|ultra` | `--max-turns` | [CLI docs](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [source](https://github.com/NousResearch/hermes-agent/blob/main/cli.py) |
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
Both result arms also expose `modelUsage` and `total_cost_usd`. Prat reports the ordered map keys
without inspecting unused per-model values, and passes through the finite nonnegative native total
without combining it with per-model figures.

### Codex

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
The ordered `stats.models` keys are also reported as observed models. Gemini exposes no verified
native USD total in this interface.

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
Each step-finish part also has a native `cost`. Prat applies the same latest-snapshot rule by part ID
and sums distinct current steps once. Missing/null latest cost makes the aggregate unknown; malformed
non-null values or aggregate overflow are protocol errors. Run events expose no verified model ID.

### Kiro

Docs advertise `--output-format stream-json` on V2/V3, but omit event schemas. V3 documentation
also says classic `kiro-cli chat` does not support V3, so do not silently force an engine.
Native exit codes include 0 success, 1 failure, 3 mandatory MCP startup failure.
Do not confuse model-list `--format json` with chat format or build a structured decoder from
convenient guessed fixtures.

Static inspection of the [official Kiro CLI 2.21.2 archive](https://prod.download.cli.kiro.dev/stable/2.21.2/kirocli-aarch64-linux.zip), whose checksum matched the [official stable manifest](https://prod.download.cli.kiro.dev/stable/latest/manifest.json), verifies that the native chat arguments include invocation-scoped `--model`. The archive identifies Clap 4.5.60; its [matching parser source](https://github.com/clap-rs/clap/blob/v4.5.60/clap_builder/src/parser/parser.rs) switches to positional-only parsing after `--`, establishing the end-of-options behavior used here. This was static package and parser verification, not a live Kiro execution.

Prat uses the documented noninteractive text response with wrapping disabled and protects the
positional prompt with the native end-of-options marker. It returns the complete stdout text,
apart from terminal line endings, because the docs do not guarantee that banners and tool progress
are separated from the final answer. Usage stays null. The prompt travels in argv and inherits the
operating system's lower size limit. Current headless mode requires `KIRO_API_KEY`; Prat inherits
native authentication and does not inspect or modify it. The documented reasoning enum is passed
through `--effort`; the resolved model is passed through `--model`.

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
Root completed `assistant.message.data.model` identifies an observed model. Prat keeps distinct IDs
in order and excludes model fields on deltas, subagent messages, and unrelated request/config data.
The terminal credits metric is not USD and is not exposed as `cost_usd`.

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
Payload entries are objects with optional `text`, `mediaUrl`, `mediaUrls`, `isError`, `isReasoning`,
and `isCommentary` fields of their native types; additive fields remain allowed. A native error
object or payload marked `isError: true` is failure evidence even when `ok` or `status` contradicts
it.

Nullable native `provider`, `model`, and `costUsd` provide accounting. Prat joins provider/model when
both exist, uses the model alone when provider is absent, and reports no model when the provider has
no model. It passes through only finite nonnegative native USD cost.

Prat passes its exact positive deadline to the process runner. Because current `agent exec`
accepts only whole-second timeout strings and internally ceilings milliseconds, its native timeout
hint is the configured value rounded upward; the wrapper's exact deadline remains authoritative.
Explicit native fallback entries require an explicit model and stay under OpenClaw's ordered
fallback behavior. Prat does not add fallbacks or retry a completed invocation.

### Hermes

Do not use `hermes -z` as the default. [oneshot.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/oneshot.py)
unconditionally enables `HERMES_YOLO_MODE` and `HERMES_ACCEPT_HOOKS`, and can exit 0 after a
nonempty failed result. The quiet chat path preserves the separate explicit `--yolo` control,
prints final response text on stdout and diagnostics/session id on stderr, checks `result.failed`
for exit 1, and exits 130 on interruption. Quiet output has no usage JSON, so usage is null.

Current parser and dispatch source verifies that chat accepts `--reasoning`, forwards it through
`cmd_chat`, and applies it to this run. Prat accepts only the canonical values
`none|minimal|low|medium|high|xhigh|max|ultra`, because Hermes otherwise warns and retains its
default for an unknown value. `--max-turns` remains the native tool-loop limit. `--run-budget` is
a separate native wall-time hint available through trusted native arguments, not a token cap.

## Remaining factual verification

- Kiro authentic JSONL schema remains unavailable; the adapter intentionally uses documented text
  output with the limitation above.
- Most agents are absent locally; fixture coverage must be distinguished from live verification.

## Source acquisition notes for implementers

Use `gh` to read GitHub sources. Root cached Codex source under
`.cache/research/codex-exec-events.rs` for this checkout. `jina-fetch` caches docs and prints
their exact paths; extract short verbatim anchors from those cached files when schema text
matters. Never use a summarizer's reconstructed command as sole evidence for exact argv.
