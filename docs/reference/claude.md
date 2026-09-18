# Claude Code

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Claude Code 2.1.266.

## Native contract

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
Whole-document parsing is bounded and rejects duplicate keys, excessive numeric width/nesting,
nonfinite numbers and invalid Unicode anywhere in the document.

## Sources

- [CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/claude.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Extra directories (verified 2026-09-18)

The [CLI reference](https://code.claude.com/docs/en/cli-reference) documents `--add-dir`
with `../apps ../lib` and requires existing directories. Prat forwards each resolved path as
an individual `--add-dir PATH` occurrence, preserving native file-access permissions.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Appended instructions (verified 2026-09-18)

The [CLI reference](https://code.claude.com/docs/en/cli-reference) describes
`--append-system-prompt`: “Append custom text to the end of the default system prompt”.
Prat uses `--append-system-prompt=TEXT`, including for validated file contents, retaining native
built-in guidance. Existing native append text remains supported alone; an active public form
conflicts with native append text or file flags.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Tool availability (verified 2026-09-18)

The [CLI reference](https://code.claude.com/docs/en/cli-reference) states: “The flag doesn’t affect
MCP tools”. `--tools` selects built-ins; an empty string selects none, but `EndConversation` can
remain while MCP tools remain. The native `default` preset selects the default set. Deny rules
via `--disallowedTools` / `--disallowed-tools` remove bare-name matches, while scoped rules such
as `Bash(rm *)` deny matching calls and retain the tool. `EndConversation` has a documented
exception while other tools remain.

The [official Python SDK transport](https://github.com/anthropics/claude-agent-sdk-python/blob/e773e44608c4873f30c8ebf78d69410e2a8b0901/src/claude_agent_sdk/_internal/transport/subprocess_cli.py)
constructs both `tools` and `disallowed_tools` with comma joins, and passes an empty string for an
empty `tools` list. Prat uses `--tools=LIST` and `--disallowedTools=LIST`; commas inside entries are
rejected. Allowlist names reject Unicode whitespace; deny patterns preserve internal spaces.
Prat never maps availability to `--allowedTools`, which grants automatic permission and can opt
in task-tracking tools. Explicit native permission denials continue to apply.

Equivalent native flags conflict only when the corresponding public list is active. Empty
`disabled_tools` clears Prat inheritance without adding a native deny flag. Evidence is source
inspection and fake argv tests, not an authenticated vendor run.
