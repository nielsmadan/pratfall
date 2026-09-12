# Agent interface evidence

Verified 2026-09-09 for the initial implementation. This is a working evidence register;
adapter tasks replace uncertainties with verified contracts. Installed help was read without
model inference. Native CLI releases can change these interfaces.

Fast mode and version probes were established on 2026-09-10 through primary-source review and
fake-executable tests, not new live native runs. Codex 0.153.4's
[configuration schema](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/config.schema.json)
accepts `service_tier` strings; Prat maps true to the invocation override
`-c service_tier="priority"` and false to `-c service_tier="default"`. Claude's
[fast-mode guide](https://code.claude.com/docs/en/fast-mode#toggle-fast-mode) documents
noninteractive `--settings '{"fastMode": true}'`, and its
[CLI reference](https://code.claude.com/docs/en/cli-reference) says inline
settings apply to that session; Prat passes the same single-key object with either boolean. Omission
adds no override. Native account and model restrictions still apply, and settings files are never
changed.

Catalog entries use `--version` except Amp, whose diagnostic is the `version` subcommand. The configured prefix is kept
literal. Prat treats the stripped stdout, or stderr when stdout is empty, as opaque strict UTF-8 and
does not infer authentication. A nonzero exit, empty selected output, invalid selected encoding,
timeout, or output overflow becomes a per-agent `version_error`. The default doctor path performs
discovery only.

Native accounting mappings were established on 2026-09-10 through the linked primary sources and
fake-executable decoder tests, not new live native runs. Prat exposes only observed model
identifiers and native USD cost fields described below. Missing data remains null; requested models,
published prices, and non-USD credits are not used as substitutes.

| Agent | Native invocation | Model | Effort | Native limits | Evidence |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `claude -p --output-format json` | `--model` | `--effort` | `--max-budget-usd`, `--max-turns` | Installed 2.1.266, [CLI reference](https://code.claude.com/docs/en/cli-reference), and [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage) |
| Codex | `codex exec --json` | `--model` | `-c model_reasoning_effort="LEVEL"` | No verified total-token/spend cap | Installed 0.153.4, [noninteractive docs](https://developers.openai.com/codex/noninteractive), [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs) |
| Gemini | `gemini --output-format json --prompt=PROMPT` | `--model` | Unsupported | None verified | [Headless reference](https://geminicli.com/docs/cli/headless) and [UI telemetry source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts) |
| Antigravity | `agy --input-format stream-json --output-format stream-json` with one stdin user event; requires 1.1.15+ | `--model` | `--effort low\|medium\|high` | No verified native timeout for stdin stream mode | [1.1.15 changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md) and [stdin stream docs](https://antigravity.google/docs/cli/headless#stream-prompts-from-stdin); installed 1.1.11 predates this interface |
| Copilot | `copilot --output-format=json --prompt=PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | `--max-ai-credits`, soft per-response cap | [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) and published 1.0.83 package source |
| Kiro | `kiro-cli chat --no-interactive --wrap never -- PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | None verified | [CLI reference](https://kiro.dev/docs/reference/cli-commands/), [headless](https://kiro.dev/docs/cli/headless/), and statically inspected 2.21.2 package |
| Cursor | `agent --print --output-format json agent -- PROMPT` | `--model` | Unsupported | None verified | [Parameters](https://cursor.com/docs/cli/reference/parameters), [output format](https://cursor.com/docs/cli/reference/output-format), and published 2026.09.08 package source |
| OpenClaw | `openclaw agent exec --json --message-file -` | `--model provider/model` | `--thinking` | Native timeout in seconds | [Agent exec](https://docs.openclaw.ai/cli/agent) and [result projection](https://github.com/openclaw/openclaw/blob/main/src/commands/agent-exec-result.ts) |
| Hermes | `hermes chat --oneshot --quiet --query-file -` | `--model`, `--provider` | `--reasoning none\|minimal\|low\|medium\|high\|xhigh\|max\|ultra` | `--max-turns` | [CLI docs](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [source](https://github.com/NousResearch/hermes-agent/blob/main/cli.py) |
| OpenCode | `opencode run --format json` with stdin prompt | `--model provider/model` | `--variant` (provider-specific string) | None verified | Installed 1.18.29 help, [run emitter](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts), and [session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts) |
| OpenHands | `openhands --headless --json --task=PROMPT` | Unsupported | Unsupported | None verified | [CLI 1.16.0 / SDK 1.21.0](#openhands-1160-sdk-1210) |
| Warp | `oz agent run --output-format ndjson --prompt=PROMPT` | `--model` | Unsupported | None verified | [Legacy Oz source](#warp-legacy-oz-interface) |
| iFlow | `iflow --prompt=PROMPT` | `--model` | Unsupported | None verified | [0.5.19 package](#iflow-0519) |
| Qwen Code | `qwen --output-format stream-json` with stdin | `--model` | Unsupported | `--max-session-turns` | [0.23.3 source](#qwen-code-0233) |
| Amp | `amp --execute --stream-json` with stdin | Unsupported | Unsupported | None verified | [Official docs](#amp) |
| Reasonix | `reasonix run --output-format json` with stdin | `--model` configured provider | `--effort` | Native `--max-steps`, not normalized turns | [1.38.5 source](#reasonix-1385) |
| Droid | `droid exec --output-format json` with stdin | `--model` | `--reasoning-effort` | None verified | [0.209.0 baseline](#droid-02090-baseline) |
| Kimi CLI | `kimi --print --input-format text --output-format stream-json --final-message-only` with stdin | `--model` | Unsupported | None verified | [1.50.0 source](#kimi-cli-1500) |
| Mistral Vibe | `vibe --prompt --output json` with stdin | Unsupported | Unsupported | `--max-turns`, `--max-price` | [2.25.2 source](#mistral-vibe-2252) |
| Crush | `crush run --quiet` with stdin | `--model` | Unsupported | None verified | [0.93.1 source](#crush-0931) |
| Devin | `devin -p -- PROMPT` | `--model` | Unsupported | None verified | [3000.10.21 baseline](#devin-30001021-baseline) |
| Cortex Code / CoCo | `cortex exec --file -` with stdin | `--model` | `--effort minimal\|low\|medium\|high\|max` | `--max-turns` | [1.1.78 baseline](#cortex-code-coco-1178-baseline) |

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
The documented five-minute `--print-timeout` examples use print mode through `-p`. They do not
establish timeout behavior for stdin stream mode. Antigravity 1.1.28 also changed print-timeout to
return partial output with a warning and successful exit, so Prat does not infer timeout from
native stderr or forward that print-only option.

The native changelog introduces stdin stream mode in 1.1.15. The locally recorded 1.1.11 predates
that interface and is not live evidence for it. The documented mode consumes prompts until stdin
closes and returns one result per prompt. Prat writes one `user` event, closes stdin, requires an
`init` followed by exactly one terminal `result`, and enforces its configured outer deadline. Only the
terminal response is returned; step text, tools, and subagent metadata are omitted. Its cumulative
single-turn usage maps input, cache-read, output, and thinking counts directly. `SUCCESS`
completes; failure states remain provider failures and `WAITING` or `RUNNING` cannot serve as
terminal evidence. Native stdin-stream timeout semantics remain unverified.

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
Each step-finish part also has a native `cost`, as recorded by the
[session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts).
Prat applies the same latest-snapshot rule by part ID and sums distinct current steps once.
Missing/null latest cost makes the aggregate unknown; malformed non-null values or aggregate
overflow are protocol errors. Run events expose no verified model ID.

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

The native
[result projection](https://github.com/openclaw/openclaw/blob/main/src/commands/agent-exec-result.ts)
exposes nullable `provider`, `model`, and `costUsd` accounting. Prat joins provider/model when both
exist, uses the model alone when provider is absent, and reports no model when the provider has no
model. It passes through only finite nonnegative native USD cost.

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

## A-tier contracts frozen on 2026-09-11

The following contracts were established by static primary-source and package inspection before
writing their adapters and independent fake-native fixtures. No native agent, including help or
version, was executed. Fixtures establish Prat's behavior against these contracts, not compatibility
with every installed release. Version probes use `--version` except Amp's `version`; normal
doctor still only locates executables. Accounting is normalized only where the individual
contracts below establish a mapping; missing fields remain null.

### OpenHands 1.16.0 / SDK 1.21.0

The [CLI manifest](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/pyproject.toml)
pins `openhands-sdk==1.21.0`. The
[parser](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/argparsers/main_parser.py)
accepts argparse string `--task`; Prat sends `--headless --json --task=PROMPT`, with empty stdin.
The equals form protects dash-leading and multiline prompt data. The
[prompt helper](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/utils.py)
uses the task directly; native `--file` adds context instructions and therefore cannot transport
Prat's input file unchanged. OS argv limits can reject a prompt below Prat's input limit.

The [entrypoint](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/entrypoint.py)
and [terminal compatibility check](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/terminal_compat.py)
print a fixed non-TTY warning and `TTY_INTERACTIVE` override hint to stdout even in headless mode.
[Setup](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/setup.py)
prints `Initializing agent...`, optional `✓ Hooks loaded`, and `✓ Agent initialized with model:`
followed by the native model. Prat discards this banner and any Rich-wrapped model continuation
lines until the next SDK record or known status line; it never uses banner text as model accounting.
The [environment warning](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/stores/agent_store.py)
uses stderr. The entrypoint prints goodbye, conversation ID and resume hints after the summary;
these remain in the discarded summary section. Unexpected startup failures still fail through
native exit or missing terminal evidence.

The same helper prints `json.dumps(event.model_dump())` as individual lines. The
[runner](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/tui/core/conversation_runner.py)
also prints `Agent is working` and `Agent finished`. The
[headless app](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/tui/textual_app.py)
then prints a Rich rule headed `CONVERSATION SUMMARY`, counts, and a panel containing arbitrary
last-message text. JSON mode does not suppress that summary. Prat accepts the verified initialization and status framing
and discards the bounded summary section beginning with its rule, without parsing echoed panel
text as SDK events. Other prose before the summary is a protocol error. Every physical line,
including discarded prose, has the existing event-byte bound; retained text and diagnostics share
StateBudget. This framing is confined to the OpenHands consumer.

[SDK response dispatch](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/agent/response_dispatch.py)
finishes on `kind: MessageEvent`, `source: agent`, `llm_message.role: assistant`, with at least one
nonblank text block. Prat concatenates `llm_message.content` blocks having `type: text` and string
`text`, excluding image/reasoning data. The dispatcher's no-content handler also emits well-formed
empty or reasoning-only agent messages before a corrective user message and continued work.
Validated messages without nonblank text are nonterminal; a later terminal event is still required
at EOF. Malformed assistant content remains a protocol error. Alternatively,
[FinishAction](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/tool/builtins/finish.py)
is terminal: `kind: ActionEvent`, `source: agent`, `tool_name: finish`,
`action: {kind: FinishAction, message: STRING}`. A second terminal event is a protocol error;
first terminal text survives. Finish can itself describe inability to perform a task, so Prat
makes no success judgment from generated prose. Native exit zero alone is insufficient.

[ConversationErrorEvent](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/event/conversation_error.py)
has `code` and `detail` strings and indicates a conversation-level failure, even when the CLI
catches the SDK exception and exits zero. It outranks protocol errors and retains earlier text.
`AgentErrorEvent` is a recoverable tool observation. Other known SDK kinds (system, observation,
state, token, streaming, condensation, hook, completion-log, pause and ACP events) carry no final
answer here. Unknown kinds and malformed relevant fields fail closed.

The only accepted native option is `--override-with-envs` (arity zero), verified in the
[shared parser](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/argparsers/util.py).
It selects already-configured native environment model/auth settings. There is no direct model,
effort, fast or normalized budget flag. Prompt/file, headless/JSON, session/resume/last, config,
confirmation and administrative selectors are reserved or rejected. Headless forces `NeverConfirm`
and disables the critic; Prat preserves that automatic approval behavior. Existing native setup is
required; this adapter neither performs setup nor changes native settings.

### Warp legacy Oz interface

The [official CLI reference](https://docs.warp.dev/reference/cli/) documents local `oz agent run`,
model selection and legacy support through the end of September 2026. Source is pinned to
[6f575836c02bd80a4b2de2755e952bec1793d3df](https://github.com/warpdotdev/warp/tree/6f575836c02bd80a4b2de2755e952bec1793d3df)
(checked 2026-09-11), rather than an unverified installed binary version. The
[Clap arguments](https://github.com/warpdotdev/warp/blob/6f575836c02bd80a4b2de2755e952bec1793d3df/crates/warp_cli/src/agent.rs)
and [global parser](https://github.com/warpdotdev/warp/blob/6f575836c02bd80a4b2de2755e952bec1793d3df/crates/warp_cli/src/lib.rs)
establish `agent run --output-format ndjson --prompt=PROMPT`, empty stdin, and optional
`--model VALUE`. Prompt data remains literal argv and inherits OS argument-size limits.

The [NDJSON emitter](https://github.com/warpdotdev/warp/blob/6f575836c02bd80a4b2de2755e952bec1793d3df/app/src/ai/agent_sdk/driver/output.rs)
emits `type: agent, text: STRING` for completed agent messages. Prat joins these messages in order
with newlines; there is no message ID or terminal-result record, so repeated text is preserved.
Native success and EOF complete the stream, including an empty stream. `agent_reasoning` is
separate; `tool_error` is recoverable. Known tool, todo, subagent, system and artifact records are
excluded. Unknown types and malformed agent records are protocol errors, retaining earlier text.
No synthetic terminal marker or inferred prose error is used.

Accepted native flags are `--name`/`-n` (one value), `--strict-mcp-startup` (zero), and
`--mcp-startup-timeout` (one value). Prompt/file/saved-prompt, output/model, cwd/config/profile,
conversation, cloud/environment/runner/executor/harness, sharing and session lifetime controls are
reserved or rejected. Native authentication and permission defaults remain active. There is no
verified one-shot replacement using the newer `warp` TUI. Recheck these sources at release time;
Prat does not remove this adapter by date or silently substitute another command.

### iFlow 0.5.19

The [published npm metadata](https://registry.npmjs.org/@iflow-ai/iflow-cli/0.5.19)
identifies `bundle/entry.js`, which loads `bundle/iflow.js`. The downloaded package's SHA-1 is
`d406e81748593c37ef464ff99cc5b495d77a76ce`; its bytes were inspected without installing or
executing them. Its bundled CLI declares `prompt`/`p` and `model`/`m` as string options. The
bundled parser handles `--KEY=VALUE` using a suffix match accepting newlines (`[\s\S]*`), and the
CLI's own prompt preprocessing converts dash-leading split prompt arguments to equals form.
This establishes exact `--prompt=PROMPT`, empty stdin and optional `--model VALUE`, including
Unicode, leading dashes, literal shell syntax and newlines. OS argv limits still apply.

Prat captures bounded native stdout, removing only terminal CR/LF. It cannot promise final-only
text or infer provider failures from prose; native exit status decides success. Accepted native
flags `--thinking`, `--plan`, and `--default` each take zero values. These are native modes, not a
generic effort ladder. Prompt/interactive/continue/resume, model, output/file/stream/ACP/server,
config/cwd and budget/timeout selectors are reserved or rejected. No normalized budgets are
exposed in this initial adapter.

Static configuration assembly resolves approval mode from native settings first, then explicit
modes; otherwise a noninteractive prompt selects `YOLO`. Thus the pinned package's default is
verified automatic approval, replacing the earlier uncertainty. `--default` and `--plan` can be
chosen explicitly through native arguments; Prat adds neither an approval mode nor a bypass.
Native startup can update its own settings; Prat does not perform or suppress that native behavior.
The [official retirement FAQ](https://vibex.iflow.cn/t/topic/4819) dates maintenance end to
2026-03-20 and hosted service shutdown to 2026-04-17, and confirms existing installations can
continue with custom APIs. Existing BYOK configuration is required; Prat does not migrate it.

### Qwen Code 0.23.3

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

### Amp

The [streaming schema](https://ampcode.com/docs/cli/streaming-json), checked 2026-09-11,
establishes `amp --execute --stream-json` with plain stdin. Only `--stream-json-thinking` (zero
values) is accepted as an optional native flag. Input/output, model, config/cwd, thread/resume,
executor/orb/runner and administrative selectors are reserved or rejected. Generic model, effort,
fast and budgets are unsupported; native mode is not a model identifier. The documented
[version probe](https://ampcode.com/docs/cli) is `amp version`.

Amp's last message is one result: success/false with a result string, or
error_during_execution/error_max_turns/true with an error string. Matching system errors also
fail. Duplicate results and records after a result are protocol errors. Root assistant text can
survive failure; child text, tool, thinking and redacted-thinking blocks are excluded. Optional
result usage maps input/output/cache-read/cache-creation counts; absent accounting stays null,
without summing assistant snapshots. Reported models and USD cost remain unknown. JSONL bounds
apply. Fixtures were accepted only after this schema review.

[Execute mode](https://ampcode.com/docs/cli/execute-mode) inherits native authentication. The
[current permission default](https://ampcode.com/news/neo) executes tools without prompts unless
existing native settings enable permissions. Prat adds no bypass and performs no native setup.

### Reasonix 1.38.5

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

### Droid 0.209.0 baseline

[Exec documentation](https://docs.factory.ai/droid-exec/overview), checked 2026-09-11, establishes
`droid exec --output-format json` with plain stdin, `--model`, and `--reasoning-effort`.
A successful object has type result, subtype success, is_error false and string result. A true
is_error is a provider failure; native nonzero status takes precedence over decoding errors.
No accounting fields are established. Missing/malformed envelopes fail; valid text survives
optional metadata errors. Whole-document parsing is bounded and rejects duplicate keys, excessive
numeric width/nesting, nonfinite numbers and invalid Unicode.

Only `--auto` (one value: low/medium/high) is accepted as a native option. It is never injected;
exec defaults to read-only. Prompt/file/input/output, model/effort, session/fork, cwd/worktree,
config, mission/remote and administrative controls are reserved or rejected. Version probing uses
`--version`; this is not a compatibility check. Existing native authentication is required.

[Settings](https://docs.factory.ai/droid-cli/settings) documents the provider-dependent effort union
`none|dynamic|off|minimal|low|medium|high|xhigh|max`. A model accepts only its subset; custom models
control reasoning through their configured provider. These public contracts were frozen before
accepting fixtures; no native binary was executed in this task.

### Kimi CLI 1.50.0

The pinned [parser](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/cli/__init__.py)
establishes `kimi --print --input-format text --output-format stream-json --final-message-only`
with plain stdin and optional `--model`. Native stdin is trimmed. Accepted native options are
`--thinking`, `--no-thinking`, `--plan`, and `--debug` (zero values). Thinking is boolean, so
there is no generic effort mapping. Prompt/command, print/quiet/input/output, model, config/work-dir,
session/resume/continue, ACP/wire/remote and administrative selectors are reserved or rejected.
Version probing uses `--version`. Print mode implies AFK: tools are automatically approved and
questions dismissed. Prat adds no approval flag. Support targets this kimi-cli version; the
successor kimi-code is not substituted.

The [final-only printer](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/ui/print/visualize.py)
emits JSONL with role assistant and string content, without a type or terminal-result marker.
Prat keeps the latest assistant string; native exit zero plus EOF completes, including empty
stdout because the printer suppresses empty final text. Step changes clear native buffered text;
Prat can retain only text actually emitted. Repeated valid assistant messages replace earlier ones.
Malformed records fail while retaining earlier text. [Print errors](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/ui/print/__init__.py)
can write plain text before native nonzero exit; native status remains authoritative. JSONL bounds
apply. Model/accounting fields remain unknown. Source review preceded independent fixtures.

### Mistral Vibe 2.25.2

The pinned [parser](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/cli/entrypoint.py)
establishes `vibe --prompt --output json` with plain stdin. Bare `--prompt` selects programmatic
mode; the native reader trims stdin. Model/effort have no verified CLI mapping and are unsupported;
use native settings or a trusted command prefix selecting an existing `VIBE_ACTIVE_MODEL`.
`max_turns` maps to `--max-turns`; `max_budget_usd` maps to `--max-price` in dollars. Native
limits may interrupt after usage exceeds them, so Prat adds no stronger spending guarantee.
Accepted native flags `--max-tokens`, `--enabled-tools`, and `--disabled-tools` each take one value.
Max tokens counts cumulative prompt plus completion tokens; it is only native passthrough.
Prompt/output, budgets, model/effort, agent/config/cwd/worktree/session/resume/continue, harness,
setup/update and remote controls are reserved or rejected. `--teleport` is explicitly reserved:
it may synchronize/push Git state and changes JSON to a remote-history object. No remote aliases
are declared by the pinned parser. Version probing uses `--version`.

The [programmatic formatter](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/cli/programmatic.py)
emits the complete public-history array using camelCase aliases. Its text projection searches
backward for the last nonempty assistant message, joining text blocks with blank lines; empty
later assistant messages cannot replace earlier text. A valid array without assistant text can
succeed with empty output. There is no terminal event. Native errors and conversation-limit
exhaustion raise before JSON finalization; native status takes precedence. Whole JSON guards apply.
The [public history types](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/app_server/models.py)
separate message, reasoning, effect, callback, checkpoint and notice entries. Only assistant
message text is returned; tool failures or notice prose do not prove a failed conversation.
No model/accounting mapping is established. Default native agent accepts edits; programmatic
approval callbacks are denied. Existing native settings/authentication remain active. These
contracts were frozen before fixtures; no native execution or setup was performed.

### Crush 0.93.1

The pinned [run command](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/run.go)
establishes `crush run --quiet` with plain stdin and optional `--model VALUE`. Quiet hides the
spinner. Prat sends the original UTF-8 bytes; native
[`MaybePrependStdin`](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/root.go#L918)
adds two newline characters before the empty positional prompt, and `run` forwards that combined
text without trimming. The independent fake records raw stdin separately from this native prompt.
Accepted native options are `--verbose`/`-v` and `--debug`/`-d`, each with zero values;
debug is inherited from the [root command](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/root.go).
Prompt/output/model/small-model, cwd/data-dir/config, session/continue, host/channels/server,
permission-bypass and administrative selectors are reserved or rejected. Effort, fast and
normalized budgets are unsupported. The version diagnostic is `--version`.

The [local application](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/app/app.go)
automatically approves the noninteractive session. Existing provider configuration is required.
`CRUSH_CLIENT_SERVER` can select a server backend whose lifetime is not owned by Prat's child
process group; native model overrides there can update workspace preferences. Prat inherits
these native behaviors without enabling a server, adding approval flags or editing settings itself.
Output is bounded complete stdout with terminal CR/LF removed. Native exit status is authoritative;
empty text can succeed, banners/progress are preserved, and prose cannot establish failure.
Usage, reported models and USD cost remain null. Source verification on 2026-09-11 preceded fixtures.

### Devin 3000.10.21 baseline

The [command reference](https://docs.devin.ai/cli/reference/commands), checked 2026-09-11,
establishes `devin -p -- PROMPT`: the entire UTF-8 prompt is one literal argv item, with empty
stdin. `--model VALUE` and the optional native `--permission-mode VALUE` precede the delimiter.
The native permission option has arity one and is never injected. Prompt-file/stdin markers are
not inferred; OS argv-size limits apply. Print/prompt/file/output/export, model, config/cwd,
continue/resume, trust-bypass, cloud/remote/executor and administrative controls are reserved or
rejected. No generic effort, fast or budget controls are verified. Version probing uses `--version`.

Print requires an already trusted workspace and existing native authentication. The
[permission reference](https://docs.devin.ai/cli/reference/permissions) describes configurable
tool approvals, but does not establish every print-mode approval outcome. Prat retains the native
behavior and makes no stronger claim. It captures bounded native stdout, removing only terminal
CR/LF; native exit status determines success, including empty successful output. Generated error
prose has no semantic meaning to the decoder. Usage, reported models and cost remain null.
These public contracts were frozen before accepting fixtures; no native CLI was executed.

### Cortex Code / CoCo 1.1.78 baseline

The [CLI reference](https://docs.snowflake.com/en/user-guide/cortex-code/cli-reference), checked
2026-09-11, establishes executable `cortex`, invocation `exec --file -`, and plain stdin.
Global `--model`, `--effort minimal|low|medium|high|max`, and `--max-turns` map the corresponding
Prat options; native turns count each conversation round. Optional `--connection`/`-c` takes one
value selecting an existing Snowflake connection. Global options precede `exec`. No fast or
spending controls are verified. Prompt/print/file/output, model/effort/turns, workdir/config,
session/resume/continue/private, plan/bypass, cloud/remote/executor and administrative controls
are reserved or rejected. `--github` implies cloud execution and is also blocked. The version
diagnostic is `--version`.

Exec disables plan mode and rejects interactive asks. Existing Snowflake account, connection and
[native authentication and policies](https://docs.snowflake.com/en/user-guide/cortex-code/security)
are prerequisites. Native startup can update its own installation/settings; Prat performs no setup
or configuration changes. JSONL framing/schema is unverified, so output is bounded complete native
stdout with terminal CR/LF removed, including any banners/progress. Native status determines
success; empty output can succeed, prose failures are not guessed, and no accounting is inferred.
These contracts were frozen before fixtures, without native execution.
