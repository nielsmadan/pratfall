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

## Native agent selection (verified 2026-09-18)

The [CLI reference](https://code.claude.com/docs/en/cli-reference) describes `--agent` as:
“Specify an agent for the current session (overrides the `agent` setting)”. Prat forwards
`native_agent` with `--agent=NAME`, preserving the complete name as one argument. Native-only
`--agent` remains accepted; it conflicts with an active public selection. Native `--agents`
definitions remain a distinct accepted passthrough option. Prat discovers no names and writes
no definitions. Native persona configuration, including permission behavior, remains authoritative.
Verification used documentation and fake executable argv tests, not authenticated execution.

## Session id (verified 2026-09-19)

The [CLI reference](https://code.claude.com/docs/en/cli-reference) documents `--session-id` as
"Use a specific session ID for the conversation (must be a valid UUID)". Prat forwards
`session_id` as `--session-id=ID` and applies no UUID rule of its own, leaving format errors to
the native CLI. Native-only `--session-id` stays reserved, so an active public value cannot be
shadowed.

The `--output-format json` result envelope carries `session_id`, which Prat reads into
`native_session_id`. A blank, non-string or NUL-bearing value is ignored rather than reported.
When the envelope never arrives - a native nonzero exit, a timeout, a signal - the requested id
is reported instead, because that is the only remaining way to locate the native transcript.
The [CLI reference](https://code.claude.com/docs/en/cli-reference) lists `--name` separately
from `--session-id`; it is accepted as a native passthrough for labelling a session in the
resume picker, and is a human label rather than the id. Prat neither sets nor reads it.
Verification used documentation and fake executable argv tests, not authenticated execution.

## Schema output (verified 2026-09-18)

The [headless guide](https://code.claude.com/docs/en/headless#get-structured-output) states that
structured output is in the `structured_output` field. Prat maps `--schema PATH` to inline
`--json-schema JSON` alongside its existing `--output-format json`. It validates and reads only
the selected source before acquiring input. Native-only `--json-schema` remains compatible;
an active public schema rejects the native duplicate.

The [SDK error contract](https://code.claude.com/docs/en/agent-sdk/structured-outputs#error-handling)
says of success without structured output: “Treat that case as a failure as well.” Prat requires
field presence, accepts every JSON value including null, and does not require `result: string`
in schema mode. It retains native `error_max_structured_output_retries` diagnostics and accounting.
Ordinary mode keeps its string-result contract. Schema decoding errors clean the owned process
group after parent exit without overriding native exit, timeout or interruption precedence.

The same SDK guide establishes draft-07 validation and support for all basic JSON types;
`format` is an unenforced annotation. No additional Claude root restriction is imposed locally.
Native versions before 2.1.205 could silently ignore invalid schemas; a missing structured answer
still fails in Prat. Prat adds no retries, full schema validator or network reference resolution.
Evidence consists of official documentation and fake-process tests; no inference was executed.
