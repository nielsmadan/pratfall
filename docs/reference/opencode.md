# OpenCode

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** OpenCode 1.18.29; OpenCode 2 contract verified against 2.0.32 on 2026-10-18 (see
[OpenCode 2](#opencode-2-verified-2026-10-18)).

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

## OpenCode 2 (verified 2026-10-18)

OpenCode 1 and 2 ship side by side: the [installer](https://opencode.ai/install) only installs
OpenCode 1, and v1.19.3 is still GitHub's Latest release, while 2.x is tagged separately. Their
`run` parsers reject each other's flags, so before every OpenCode run, and for `--dry-run`, Prat
runs the configured prefix with `--version` (15-second deadline outside the run's `--timeout`,
the doctor's output bounds and empty stdin) and reads the first `MAJOR.MINOR` in the text. 2 or later selects the OpenCode 2 argv;
an older major keeps the 1.x argv above. Unrecognized text fails with `protocol_error`, and a probe
that cannot start, exits nonzero or times out fails the run before launch with the probe's code.

The [2.0.32 command spec](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/cli/src/commands/commands.ts)
has no `--variant`, `--pure`, `--log-level`, `--dir`, `--port`, `--attach`, `--share` or
`--interactive`; installed 2.0.32 fails with `Unrecognized flag: --variant in command opencode run`.
On 2.x Prat therefore rejects native `--pure` and `--log-level` as unknown; `--print-logs` is a
global 2.x flag and stays accepted. Its `--model` takes `provider/model#variant`, so Prat appends
effort as `#EFFORT`. Effort without a model, or with a model that already names a `#variant`, is
rejected because 2.x cannot select a variant otherwise from the command line. The probe and these
checks run before the prompt is read, and the error names the option's origin, so an effort set in
a profile reports `invalid_config` like any other profile error.

By default 2.x runs through a shared background service that outlives the command, so a Prat
timeout or kill would leave the session executing there. Prat passes `--standalone`, which
[starts a private `opencode serve --stdio`](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/cli/src/services/standalone.ts)
whose lease ends when the parent's stdin pipe closes. The service has no environment-variable
switch, and 1.x's strict parser rejects `--standalone`, which is why the argv depends on the probe.
`--standalone` and `--server` are reserved native flags.

The [2.0.32 emitter](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/cli/src/run/noninteractive.ts)
keeps the 1.x `text`, `step_finish`, `reasoning` and `tool_use` records, with the same finish
reasons, `cost` and `tokens: {input, output, reasoning, cache: {read, write}}`. Errors changed to
the [structured error](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/schema/src/session-error.ts)
`error: {type, message, status?, response?}`; Prat reports its `message` as the provider error
and still accepts the 1.x `{name, data}` form. Failures before a session exists use the same shape
with an empty `sessionID`, for example
`{"type":"error",...,"error":{"type":"unknown","message":"You must provide a message"}}`.
`tool_use` records report tool activity in progress output for both versions.

2.x usually drops the final `step_finish`
([anomalyco/opencode#53207](https://github.com/anomalyco/opencode/issues/53207), open). The
emitter sets `finalizing` once `session.wait` reports idle and then skips every queued event except
`session.execution.*`; `reconcile()` replays missing text, reasoning and tool parts from the stored
messages but never a `step_start` or `step_finish`. A live 2.0.32 run against
`openrouter/xiaomi/mimo-v2.6-pro` ended on `text` with exit 0 and no `step_finish`. For 2.x, Prat
therefore treats a stream whose last step produced text and ended without a finish as complete, and
reports usage and cost as unknown rather than summing only the earlier steps. Error events still
take precedence, a stream that ends after a `tool-calls` finish with no further text is still
incomplete, and a `stop` finish that does arrive is used as before. 1.x keeps the strict rule.

Evidence is primary source at tag v2.0.32, installed 2.0.32 help and flag errors, a live 2.0.32
run outside the sandbox, and fake argv and stream tests.

## Sources

- [run emitter](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts)
- [session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts)
- [2.0.32 run command](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/cli/src/run/run.ts)
  and [JSON emitter](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/cli/src/run/noninteractive.ts)

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
