# Hermes

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Native package version not recorded; linked upstream sources define this contract.

## Native contract

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

## Sources

- [CLI docs](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)
- [source](https://github.com/NousResearch/hermes-agent/blob/main/cli.py)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/hermes.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Native attachments (verified 2026-09-18)

Current [parser source](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/_parser.py)
declares `add("--image", help="Optional local image path to attach to a single query")`.
It is a scalar argparse option: repeated values would retain only the last. Prat therefore accepts
one public image, emits `--image=PATH`, and rejects larger arrays before reading prompt input.
The existing `--query-file -` task transport stays independent. Active attachments conflict with
native `--image`; native-only use remains accepted.

Evidence was inspected from upstream main on this date; no native execution was performed.
Native image formats, decoding and vision-capable model requirements remain authoritative.
