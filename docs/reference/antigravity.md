# Antigravity

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Documented stdin mode requires 1.1.15+; installed 1.1.11 predates it.

## Native contract

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

## Sources

- [1.1.15 changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md)
- [stdin stream docs](https://antigravity.google/docs/cli/headless#stream-prompts-from-stdin)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/antigravity.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
