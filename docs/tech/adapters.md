# Adapter reference

Each adapter validates trusted native arguments, builds an immutable invocation, and decodes native
output. `registry.py` is the only adapter assembly point and binds each callable explicitly.
Codex, Copilot, Antigravity, OpenCode, Warp, OpenHands, Qwen, Amp and Kimi expose an incremental consumer factory;
their `decode(str)` entry points feed the same state machine used during process execution.

Structured adapters use their native completion contract and treat verified semantic provider
failures as errors even when the process exits zero. Warp and Kimi use native exit plus EOF, while
OpenHands requires a terminal SDK assistant or finish event. Text adapters return bounded native
stdout with terminal line endings removed and cannot separate a final answer from native banners or progress.

The shared JSONL consumer owns strict UTF-8 decoding, physical line framing, and local bounds.
Each record is limited to 8 MiB excluding LF or CRLF, live retained strings and answer separators
share an 8 MiB state budget, and at most 16,384 live logical records are retained. Identifiers,
models, diagnostics, token/cost snapshots, and bounded numeric values count while live; snapshot
replacement refunds the prior payload. Discarded whitespace and events are still individually
bounded without a cumulative trace cap.

Capability metadata describes whether an adapter accepts `model`, `effort`, `fast`, and a small set of
native budgets. It does not catalog provider model names. The normalized `model` field records the
resolved requested model, or null when the native default was selected.

`DecodedOutput` also carries nullable `reported_models` and `cost_usd`. Adapters populate only
verified native mappings, validate exposed identifiers as nonempty UTF-8 strings and costs as
finite nonnegative numbers, and retain valid accounting alongside provider, protocol, runner, and
timeout failures. The normalizer copies these fields without replacing the requested `model`.
OpenCode stores the latest cost snapshot per step ID before summing distinct steps; any unknown
latest cost makes the aggregate unknown, and aggregate overflow is a protocol error.

Claude and Codex are the only adapters with verified invocation-only fast mappings. Their native
settings/config flags remain reserved so `native_args` cannot replace the resolved Pratfall value.
Each catalog entry also carries its version-probe arguments; Amp uses `version` and other entries use `--version`.

`native_args.py` accepts a finite per-adapter set of known flags with explicit arity. It rejects
response files, positional arguments, subcommands, and fields owned by Pratfall. A trusted wrapper
is the escape hatch for other native options. See [agent evidence](agent-evidence.md) for native
versions, schemas, and source links.

OpenHands uses a dedicated ByteConsumer and StateBudget. It accepts its pinned CLI's initialization
banners, status lines and the trailing Rich conversation summary; arbitrary summary panel contents are
never reparsed as SDK events. The shared JsonlConsumer remains strict JSONL. Both OpenHands and
Warp fail closed on unknown event kinds/types. iFlow uses bounded complete stdout capture.

Qwen accepts intermediate result failures and lets the final native result govern; Amp requires
one terminal result. Their error-object and error-string schemas are decoded independently.
Both use final result accounting without adding assistant snapshots. Reasonix parses one bounded
JSON document and requires success subtype, rejecting paused or incomplete outcomes even when
is_error is false. Its cache-miss and non-USD compatibility aliases are never normalized as
cache-write tokens or USD cost.

Droid and Vibe share bounded whole-JSON framing/Unicode/numeric guards while decoding their own
schemas. Droid requires a successful result envelope; Vibe projects the last nonempty assistant
text from a complete public-history array, joining text blocks with blank lines. Vibe can complete
with no assistant text. Kimi uses the shared JSONL consumer with its local role discriminator and
keeps the last assistant content string. Its final-only native printer suppresses empty text,
so native success and EOF can complete without a record. None of these three protocols has verified
accounting mappings. Native nonzero exit remains authoritative over decoder errors.

Crush, Devin and Cortex use bounded complete text capture through the existing runner. Their
decoders remove only terminal CR/LF and add no semantic failure or accounting inference.
Crush receives unchanged stdin and natively appends two newlines. Devin places one literal prompt
after `-p --`, and Cortex uses
`exec --file -` with global model/effort/turn/connection options before the subcommand. Native
nonzero exit remains authoritative. The finite native option sets preserve the selected
transport and prevent session, output, remote and administrative overrides.
