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
