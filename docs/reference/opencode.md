# OpenCode

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** OpenCode 1.18.29.

## Native contract

[run.ts](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts)
emits JSONL objects with `type`, timestamp, sessionID and a payload. Completed text parts are
`type: text`, `part: {type: text, id, text, time: {end, ...}, ...}`. `step_finish` wraps a
`step-finish` part with reason and token statistics. A tool-call step is not final completion.
Native `session.error` is surfaced as `type: error`, `error: {name,data: {message,...},...}`.
`MessageOutputLengthError` is the verified exception with empty `data`; the CLI reports its native
name when no message exists. Reasoning and tool events are separate. Native CLI waits for its own
session to become idle without emitting that idle event, so decoder completion must use the
verified step-finish reason.
Root cached source in `.cache/research/opencode-run.ts`.

The run source reads stdin whenever no positional message is supplied, so Prat uses stdin for the
prompt. Completed text parts are deduplicated by part ID. The latest token snapshot for each step ID
replaces earlier copies, and distinct steps are summed. OpenCode already separates fresh input from
cache reads/writes and text output from reasoning; Prat preserves that split. `tool-calls` continues
the run, `stop` is completion, and `length`, `content-filter`, `error`, or `unknown` finishes are
reported as incomplete provider failures.
Each step-finish part also has a native `cost`, as recorded by the
[session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts).
Prat applies the same latest-snapshot rule by part ID and sums distinct current steps once.
Missing/null latest cost makes the aggregate unknown; malformed non-null values or aggregate
overflow are protocol errors. Run events expose no verified model ID.

## Sources

- [run emitter](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts)
- [session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/opencode.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Native agent selection (verified 2026-09-18)

Public `native_agent` is unsupported. The [run command at revision 5f9d9187](https://github.com/anomalyco/opencode/blob/5f9d9187c01708b4e700c9c81c1045ab482bf31d/packages/opencode/src/cli/cmd/run.ts#L589)
returns `undefined` after warning for an unknown name or a subagent-only definition, allowing
default-agent fallback. For an unknown name the warning says “not found. Falling back to default
agent”. Prat cannot promise explicit selection using this contract. Existing native-only
`--agent` passthrough remains accepted with OpenCode's behavior. No discovery or fallback is
added by Prat. Evidence is static primary-source inspection, not authenticated execution.

## Native attachments (verified 2026-09-18)

The [run source at revision 5f9d9187](https://github.com/anomalyco/opencode/blob/5f9d9187c01708b4e700c9c81c1045ab482bf31d/packages/opencode/src/cli/cmd/run.ts)
defines `.option("file", { alias: ["f"], type: "string", array: true, ... })` and builds file parts
from each path using `path.resolve`. Prat emits repeated `--file=PATH`, with canonical regular
readable paths so native lexical normalization cannot change symlink/`..` targets. Public
attachments reject directories even though some native modes can construct directory parts.
Native `--attach` connects to a server and is unrelated to public `--attach`.

The task continues through stdin; files do not replace it. Active public attachments conflict
with native `--file` and `-f`; native-only use remains accepted. File content interpretation,
formats and model limits belong to OpenCode. Evidence is primary source inspection and fake argv
tests, without native inference.
