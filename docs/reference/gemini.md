# Gemini

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Native package version not recorded; linked upstream sources define this contract.

## Native contract

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
Whole-document parsing is bounded and rejects duplicate keys, excessive numeric width/nesting,
nonfinite numbers and invalid Unicode anywhere in the document.

## Sources

- [Headless reference](https://geminicli.com/docs/cli/headless)
- [UI telemetry source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/gemini.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
