# 0007 — Add a public flag only for a shared value

**Status:** accepted

**Recorded:** 2026-11-16

## Context

Every native CLI has controls of its own, and many look alike: a tool allowlist, a budget, a
persona. Each prat flag or config key that renames one becomes a contract that prat maps, validates
and documents for every agent it supports. Native arguments (`-- …` or `native_args`) already
reach every agent, and since 1.3.0 arguments after `--` append to a profile's `native_args`
instead of replacing them, so a profile's native flags survive a run's extra ones.

Two controls showed the cost. `--max-ai-credits` mapped one Copilot flag: a prat flag for a single
agent added nothing over its native spelling. `--tools` and `--disabled-tools` mapped six agents'
flags (Claude `--tools`/`--disallowedTools`, Qwen `--core-tools`/`--exclude-tools`, Copilot
`--available-tools`/`--excluded-tools`, Droid, Vibe and Pi), but every value was the agent's own
tool vocabulary, so no list carried from one agent to another.

A translation table (`read`, `search`, `edit`, `shell`, `web` mapped to native names) was
considered and rejected. Tools are not one-to-one (Claude Grep and Glob, Pi grep, find and ls,
Droid Grep, Glob and LS). Droid's tool set depends on the model. A deny list in a shared vocabulary
fails open when an agent adds a tool, as Droid 1.8.0 did with separate `Execute` and `Script`. Prat
would also have to track six tool catalogues per release.

## Decision

A native control becomes a prat flag or config key only when both hold:

1. At least two agents support it.
2. Its value means the same thing on every agent that supports it, so one value works unchanged
   across them.

A control that fails the second condition still qualifies when it is a single setting that
profiles pin and runs routinely override. Native arguments only append, and many native parsers
refuse a repeated flag, so a prat field that replaces cleanly is the only way to override a pinned
value per run. `--model` is the case: model ids are each agent's own, but a profile pins one and a
run often wants another.

Everything else goes through native arguments.

## Consequences

- `--max-ai-credits` (one agent), `--tools`/`--disabled-tools` (six vocabularies) and
  `--native-agent` (each agent's own names, pinned by choosing a profile) were removed in 1.3.0.
- `--effort` (shared `low`/`medium`/`high`), `--max-budget-usd`, `--max-turns`, `--add-dir`,
  `--attachment`, `--schema`, `--instructions` and `--timeout` stay, since their values mean the
  same everywhere; `--model` stays under the override clause.
- A contributor adding an agent maps only the existing public controls it supports. A new native
  control is documented as a native argument unless it meets this rule.
- Native-argument docs carry the per-agent flag names and caveats that a public flag would
  otherwise have hidden.
