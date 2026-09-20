# Grok Build baseline

**Evidence recorded:** 2026-09-14.
**Method:** Official documentation and source inspection; Grok was not executed.

## Native contract

The [headless guide](https://docs.x.ai/build/cli/headless-scripting) establishes executable `grok`,
single-turn prompt flag `-p`/`--single`, JSON output, model and reasoning-effort controls, native
agentic `--max-turns`, exit codes, authentication, and permission behavior. Prat invokes
`grok --no-auto-update --output-format json`, adds `--model`, `--reasoning-effort` and `--max-turns`
when configured, and appends the prompt as `--single=PROMPT`. The prompt is therefore subject to
the operating system's argv-size limit. Grok trims it natively.
Existing xAI API-key or OAuth authentication is required.

The native effort enum is `none|minimal|low|medium|high|xhigh|max`, with model-specific subsets.
Headless mode cancels unresolved permission requests unless a native approval mode is selected.
Prat preserves that default and never injects `--always-approve`. Explicit permission, tool, agent,
rules, sandbox, approval, structured-output and background-wait options are accepted as native
arguments. Prompt, output, model, effort, turns, session, worktree, working-directory and version
controls are reserved. Prat disables the invocation's auto-update check without changing native
configuration.

The [headless implementation](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager/src/headless.rs)
emits one JSON success object containing `text`, `stopReason`, `sessionId` and `requestId`.
`end_turn` is the only successful terminal reason; max-token, max-turn-request, refusal and
cancellation outcomes retain text but remain failures. JSON errors use `type: error` and a string
`message`.

The same object can carry disjoint fresh-input, cache-read and cache-creation counts, an output
count that includes reasoning tokens, `modelUsage`, turn count, and complete USD cost. Prat validates the token total,
reports `modelUsage` keys and accepts USD cost only when its exact integer tick companion agrees.
Missing cost remains null. Partial cost remains null. `usage_is_incomplete` becomes a protocol
error rather than exact token accounting.

Whole-document parsing is bounded and rejects duplicate keys, excessive numeric width or nesting,
nonfinite numbers, and invalid Unicode anywhere in a successful document. These contracts were
frozen before fake-native fixtures were written.

## Session id (verified 2026-09-19)

The success object contains `sessionId` alongside `text`, `stopReason` and `requestId`; Prat
already required it to be a string. It is now reported as `native_session_id`, on the
`end_turn` path and on the non-`end_turn` provider-error path alike.

The [CLI reference](https://docs.x.ai/build/cli/reference) documents `-s, --session-id <UUID>`
as "Use a specific UUID for a new session", distinct from `--resume [<ID>]` ("Resume a session
by ID") and `--fork-session` ("When resuming, fork into a new session ID"). Prat therefore
grants the `session_id` capability and forwards it as `--session-id ID`, matching the
two-token form this adapter uses for `--model`. Native `--session-id`, its `-s` alias and
`--fork-session` stay reserved against an active public value.

## Sources

- [Headless mode and scripting](https://docs.x.ai/build/cli/headless-scripting)
- [CLI reference](https://docs.x.ai/build/cli/reference)
- [Grok Build source](https://github.com/xai-org/grok-build/tree/37949780c144e37df692e3d669051a21fec24f20)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/grok.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
