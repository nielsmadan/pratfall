# Codex

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Codex 0.153.4.

## Native contract

`codex-rs/exec/src/exec_events.rs` defines tagged JSONL events: `thread.started`, `turn.started`,
`turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`, and `error`.
`item.completed.item` carries `id`, `type`, and type-specific fields. Final assistant messages
use `type: agent_message` and `text`; reasoning and command/tool contents are separate item types.
`turn.completed.usage` contains `input_tokens`, `cached_input_tokens`, `output_tokens`, with
newer optional cache-write/reasoning fields. `turn.failed.error.message` and top-level
`error.message` are failures. A terminal success event is required; EOF alone is not success.
Codex's supported events expose no verified model or cost accounting. If EOF or Prat's outer
timeout arrives after a completed assistant message but before `turn.completed`, Prat retains the
latest message after any earlier completed-turn answers and still reports failure.

## Recorded compatibility observation

On 2026-09-09, Codex 0.153.4 with `gpt-5.6-luna`, low effort and ephemeral sessions passed three
installed-wheel checks: exact text output, a multiline profile/stdin JSON response with usage,
and an exact edit to the sole file in a disposable directory. Response checks used read-only
sandboxing and 90-second deadlines; the edit used workspace-write and a 120-second deadline.
The tested wheel's SHA256 was
`1aca00c48236ecc7ffcb763c41c2518b0b1c506f0a6f7237c4969b2b9536b174`.

The edit emitted two nested-sandbox `apply_patch` failures before succeeding. Native events were
not retained, so the recovery mechanism is unknown. Later runtime changes used offline fixture
verification; these observations do not establish compatibility of the current package or newer
native versions. Repeat live checks only when authorized, recording native version, artifact hash,
argv/input, working directory, stdout/stderr, exit status and file results.

## Sources

- [noninteractive docs](https://developers.openai.com/codex/noninteractive)
- [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/codex.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Extra directories (verified 2026-09-18)

The [CLI reference](https://developers.openai.com/codex/cli/reference) defines `--add-dir`
as additional writable directories and explicitly allows repetition. Prat forwards repeated
`--add-dir PATH` without selecting a sandbox or approval mode.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Appended instructions (verified 2026-09-18)

The [configuration reference](https://developers.openai.com/codex/config-reference) describes
`developer_instructions`: “Additional developer instructions injected into the session (optional).”
Prat sends `-c developer_instructions=TOML_STRING`, replacing any native configured value for this
invocation while retaining built-in guidance. It emits a JSON-compatible TOML basic string with
Unicode scalar values unescaped and DEL escaped; UTF-16 surrogate-pair escapes are not valid TOML.
Native `-c` / `--config` remain reserved as before.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Native attachments (verified 2026-09-18)

The [shared options at revision 7498521d](https://github.com/openai/codex/blob/7498521d288b9b3b96ffba4eedf089d8d6e06a84/codex-rs/utils/cli/src/shared_options.rs)
declare `long = "image"`, `short = 'i'`, `value_delimiter = ','`, and `num_args = 1..` on
`images: Vec<PathBuf>`. Prat emits repeated `--image=PATH` with canonical regular readable paths,
rejects commas, and inserts `--` before the final stdin prompt sentinel `-` in attachment mode.
Clap documents `--` as the “Only positional args follow” idiom in its
[argument reference](https://docs.rs/clap/4.6.7/clap/struct.Arg.html#method.allow_hyphen_values).
This keeps the variadic image option from swallowing the task sentinel. Active attachments reserve
both `--image` and `-i`; existing native-only use remains accepted.

Native image decoding, vision-capable model support and payload limits remain authoritative.
Evidence is primary source inspection and fake executable argv tests; no native inference executed.
