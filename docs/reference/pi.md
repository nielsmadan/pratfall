# Pi

Verified on 2026-09-18 against official **v0.85.1** source and the installed package's static
files. No authenticated inference was run. The current package is
`@earendil-works/pi-coding-agent`; the former `badlogic/pi-mono` repository redirects to
[earendil-works/pi](https://github.com/earendil-works/pi/releases/tag/v0.85.1).

## Invocation and controls

Prat runs `pi --print --mode json` and sends the composed prompt through stdin. Pi trims leading
and trailing stdin whitespace. Stdin avoids the native positional `@file` interpretation.
The [argument parser](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/cli/args.ts)
requires separate values for built-in flags: `--model VALUE`, not `--model=VALUE`. Prat translates
accepted native `--flag=value` forms into separate argv entries without splitting value contents.

| Public control | Native mapping |
| --- | --- |
| `model` | `--model VALUE`, including `provider/model` patterns |
| `effort` | `--thinking off|minimal|low|medium|high|xhigh|max`; native model limits apply |
| `tools` | `--tools COMMA_LIST`; an empty array becomes an empty argv value |
| `disabled_tools` | `--exclude-tools COMMA_LIST` |
| `attachments` | Repeated `@ABSOLUTE_PATH` arguments |

Tool allowlists and denylists apply to built-in, extension and custom tools; exclusions apply
last. Names are exact, comma-separated and JavaScript-trimmed by Pi, so Prat rejects commas and
surrounding native whitespace in individual public values. Tool filters do not sandbox the process.
See [SDK selection](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/sdk.ts)
and [session filtering](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/agent-session.ts).

Native passthrough supports provider selection, system/append prompts, tool filters, disabling
extensions/skills/prompt templates/context files, `--offline`, and `--no-session`. Prat reserves
prompt, output, model, thinking and session-resumption controls. Unknown options and positional
arguments require a trusted executable wrapper. Active public tool controls reserve equivalent
native flags. Native session saving, authentication, extensions and permission behavior remain
Pi's own; no automatic approval flag is added.

Public instructions are unsupported because Pi can interpret a string as an existing filename.
The [resource loader](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/resource-loader.ts)
contains `if (existsSync(input))` followed by `return stripBom(readFileSync(input, "utf-8"));`.
Explicit append sources also replace discovered `APPEND_SYSTEM.md` sources. Native passthrough
keeps these semantics, for example `prat pi "review" -- --append-system-prompt rules.md`.
There are no verified public directory, persona, schema, fast-mode or budget mappings.

## Attachments

The [file processor](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/cli/file-processor.ts)
loads recognized images as images and other files as UTF-8 text with a `<file name="...">` wrapper.
Empty files are skipped. Pi appends these native text wrappers to its trimmed stdin message.
Prat validates regular readable files and passes canonical paths; native format and model limits
remain authoritative.

Pi [normalizes paths](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/utils/paths.ts)
before checking existence, converting U+00A0, U+2000–U+200A, U+202F, U+205F and U+3000 to ASCII
spaces. Prat rejects those characters in both selected paths and canonical symlink targets so a
validated attachment cannot silently select another existing file. See
[path lookup](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/tools/path-utils.ts).

## Output, completion and accounting

The [JSON guide](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/json.md)
says: “`message_end` contains the final authoritative message.” Prat retains the latest assistant
message's text blocks and ignores reasoning, tool calls, tool results, streaming deltas and repeated
snapshots in `turn_end` / `agent_end`.

Completion requires `agent_settled` and an assistant message. `agent_end` may precede native retry
or compaction continuations, so it alone is insufficient. Failed attempts followed by successful
native retries do not poison the final result. Prat adds no retries. The
[session loop](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/agent-session.ts)
emits settlement after retry/compaction/queued-followup processing.

An assistant `stopReason` of `error` or `aborted` produces a normalized provider error, even when
Pi exits zero. [Print mode](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/modes/print-mode.ts)
only converts those message states to nonzero exits in text mode. A final error without text retains
the previous completed assistant text as partial output. Invalid or unfinished streams fail with a
protocol error; output remains bounded by the shared consumer limits.

Prat sums assistant `message_end` usage exactly once across attempts and turns: `input`, `output`,
`cacheRead`, `cacheWrite`, and native USD `cost.total`. Repeated lifecycle snapshots are not summed.
Missing usage or cost makes that run total unknown. Distinct assistant `model` IDs populate
`reported_models`; the requested model stays separate. These totals cover emitted assistant
messages, not unreported native work such as compaction or extension-owned inference. Source:
[message types](https://github.com/earendil-works/pi/blob/v0.85.1/packages/ai/src/types.ts).
