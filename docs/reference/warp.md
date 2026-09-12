# Warp legacy Oz interface

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

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

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/warp.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
