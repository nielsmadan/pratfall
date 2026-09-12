# OpenClaw

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Native package version not recorded; linked upstream sources define this contract.

## Native contract

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

## Sources

- [Agent exec](https://docs.openclaw.ai/cli/agent)
- [result projection](https://github.com/openclaw/openclaw/blob/main/src/commands/agent-exec-result.ts)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/openclaw.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
