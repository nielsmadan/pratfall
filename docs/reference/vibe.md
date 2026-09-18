# Mistral Vibe 2.25.2

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The pinned [parser](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/cli/entrypoint.py)
establishes `vibe --prompt --output json` with plain stdin. Bare `--prompt` selects programmatic
mode; the native reader trims stdin. Model/effort have no verified CLI mapping and are unsupported;
use native settings or a trusted command prefix selecting an existing `VIBE_ACTIVE_MODEL`.
`max_turns` maps to `--max-turns`; `max_budget_usd` maps to `--max-price` in dollars. Native
limits may interrupt after usage exceeds them, so Prat adds no stronger spending guarantee.
Accepted native flags `--max-tokens`, `--enabled-tools`, and `--disabled-tools` each take one value.
Max tokens counts cumulative prompt plus completion tokens; it is only native passthrough.
Prompt/output, budgets, model/effort, agent/config/cwd/worktree/session/resume/continue, harness,
setup/update and remote controls are reserved or rejected. `--teleport` is explicitly reserved:
it may synchronize/push Git state and changes JSON to a remote-history object. No remote aliases
are declared by the pinned parser. Version probing uses `--version`.

The [programmatic formatter](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/cli/programmatic.py)
emits the complete public-history array using camelCase aliases. Its text projection searches
backward for the last nonempty assistant message, joining text blocks with blank lines; empty
later assistant messages cannot replace earlier text. A valid array without assistant text can
succeed with empty output. There is no terminal event. Native errors and conversation-limit
exhaustion raise before JSON finalization; native status takes precedence. Whole JSON guards apply.
The [public history types](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/app_server/models.py)
separate message, reasoning, effect, callback, checkpoint and notice entries. Only assistant
message text is returned; tool failures or notice prose do not prove a failed conversation.
No model/accounting mapping is established. Default native agent accepts edits; programmatic
approval callbacks are denied. Existing native settings/authentication remain active. These
contracts were frozen before fixtures; no native execution or setup was performed.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/vibe.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)

## Tool availability (verified 2026-09-18)

The [v2.25.2 parser](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/cli/entrypoint.py)
defines repeatable `--enabled-tools` and `--disabled-tools`. Its vocabulary includes exact names,
globs and `re:` regular expressions. Prat repeats `--enabled-tools=PATTERN` and
`--disabled-tools=PATTERN`, preserving commas, spaces, backslashes and pattern syntax as data.

The [tool manager](https://github.com/mistralai/mistral-vibe/blob/v2.25.2/vibe/core/tools/manager.py)
first filters disabled native sources, then applies the enabled patterns, then disabled patterns
to the available tools, including MCP and connectors. An empty enabled list is falsy and does
not activate the allowlist, so Prat rejects `tools=[]` instead of inventing a no-match pattern.
Native per-source disabling and permission decisions remain authoritative; Prat adds no approvals.

Equivalent native flags conflict only with active public settings; existing native-only filters
remain supported. Empty `disabled_tools` clears Prat inheritance without adding a native flag.
Verification used pinned source inspection and fake argv tests, not an authenticated native run.
