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

## Sources

- [CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/claude.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
