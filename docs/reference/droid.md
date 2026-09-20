# Droid 0.209.0 baseline

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

[Exec documentation](https://docs.factory.ai/droid-exec/overview), checked 2026-09-11, establishes
`droid exec --output-format json` with plain stdin, `--model`, and `--reasoning-effort`.
A successful object has type result, subtype success, is_error false and string result. A true
is_error is a provider failure; native nonzero status takes precedence over decoding errors.
No accounting fields are established. Missing/malformed envelopes fail; valid text survives
optional metadata errors. Whole-document parsing is bounded and rejects duplicate keys, excessive
numeric width/nesting, nonfinite numbers and invalid Unicode.

Only `--auto` (one value: low/medium/high) is accepted as a native option. It is never injected;
exec defaults to read-only. Prompt/file/input/output, model/effort, session/fork, cwd/worktree,
config, mission/remote and administrative controls are reserved or rejected. Version probing uses
`--version`; this is not a compatibility check. Existing native authentication is required.

[Settings](https://docs.factory.ai/droid-cli/settings) documents the provider-dependent effort union
`none|dynamic|off|minimal|low|medium|high|xhigh|max`. A model accepts only its subset; custom models
control reasoning through their configured provider. These public contracts were frozen before
accepting fixtures; no native binary was executed in this task.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/droid.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Appended instructions (verified 2026-09-18)

The [exec reference](https://docs.factory.ai/droid-exec/overview) describes
`--append-system-prompt <text>`: “Append custom text to end of system prompt”. It also lists a
native file variant. Prat reads the winning file itself and uses `--append-system-prompt=TEXT`
for both public forms, preserving built-in guidance. Native append text and file flags conflict
with active public instructions; the native-only allowlist remains unchanged.

Verification used primary documentation/source inspection and fake executable argv tests;
no authenticated native run was performed for this control.

## Tool availability (verified 2026-09-18)

The [exec reference](https://docs.factory.ai/droid-exec/overview) defines `--restrict-tools` as:
“Restrict the run to only the specified tools (comma or space separated list)”. It also defines
`--disabled-tools` and the separate `--additional-tools` expansion. Names are native IDs, such as
`ApplyPatch` and `execute-cli`; Prat does not translate them or add `--auto`.

Prat emits `--restrict-tools=LIST` and `--disabled-tools=LIST`, comma-joining each list and rejecting
commas or Unicode whitespace (including U+FEFF) inside IDs. A faithful empty restriction is not
established, so `tools=[]` is rejected. Equivalent native flags and `--additional-tools` conflict
with the corresponding active public controls. The native-only acceptance set remains unchanged.
Existing native permission denials remain authoritative. Verification used primary documentation
and fake argv tests; no authenticated native run was performed.

## Session id (verified 2026-09-20)

The [CLI reference](https://docs.factory.ai/droid-cli/cli-reference) documents `-s,
--session-id <id>` as "Continue an existing session" and `--fork` as "Fork and resume a session
in a new copy". Both are resume semantics, so Prat does not grant the `session_id` capability
to Droid; a one-shot run has no earlier session to continue. Both stay reserved. Droid reports
no session in its result protocol, so `native_session_id` is null.
