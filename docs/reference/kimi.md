# Kimi Code 2.1.1

**Evidence recorded:** 2026-10-13, official release source inspection. No authenticated run yet.

## Native contract

Support targets `@moonshot-ai/kimi-code@2.1.1`, executable `kimi`. The retired Python `kimi-cli`
package is incompatible. The [release parser](https://github.com/MoonshotAI/kimi-code/blob/%40moonshot-ai%2Fkimi-code%402.1.1/apps/kimi-code/src/cli/commands.ts)
accepts `kimi --output-format stream-json --prompt=PROMPT` and `--model VALUE`. Prompt text is
one literal argv value with empty stdin; OS argument-size limits apply. Whitespace-only prompts
fail native validation. Native option validation does not trim ordinary submitted prompt text.

Prat accepts native `--skills-dir` and `--add-dir` with one path per occurrence. Generic effort,
fast, budget and requested-session controls are unsupported. Legacy `--print`, `--input-format`,
`--final-message-only`, `--thinking`, `--no-thinking`, `--plan`, and `--debug` are not supported.
Native agent selection exists but is not mapped by this adapter.

The [JSONL writer](https://github.com/MoonshotAI/kimi-code/blob/%40moonshot-ai%2Fkimi-code%402.1.1/apps/kimi-code/src/cli/prompt-render.ts)
emits assistant records with optional string `content` and optional `tool_calls`, tool records,
and metadata. Prat retains the latest nonempty assistant string. Tool-only and empty assistant
messages do not erase an answer; tool results and thinking are excluded. Hook output can also
arrive as assistant text and is not distinguishable from model text in this interface.

Known metadata types are `system.version`, `turn.step.retrying`, and `session.resume_hint`.
The last carries `session_id`, reported as `native_session_id`. It may be absent on exceptions.
Native `/goal` prompts can emit a role-less `goal.summary`; Prat recognizes this metadata without
inferring token accounting from its aggregate `tokensUsed`. Unknown roles or metadata types fail
closed. JSONL byte, numeric, and retained-state limits apply.

There is no ordinary terminal-result record. EOF plus native exit zero establishes completion,
including an empty stream. The [headless runner](https://github.com/MoonshotAI/kimi-code/blob/%40moonshot-ai%2Fkimi-code%402.1.1/apps/kimi-code/src/cli/v2/run-v2-print.ts)
sets failure exit 1 and reports errors on stderr. [Goal execution](https://github.com/MoonshotAI/kimi-code/blob/%40moonshot-ai%2Fkimi-code%402.1.1/apps/kimi-code/src/cli/goal-prompt.ts)
uses 0 for complete, 3 for blocked, and 6 for paused. Native failure preserves any earlier answer.
Usage, reported models and USD cost remain unknown.

Headless execution selects native automatic approval; static deny rules remain effective. Prat
adds no permission bypass flag and does not alter native settings. Authenticate separately with
`kimi login`; see the [installation guide](https://moonshotai.github.io/kimi-code/en/guides/getting-started).

## Verification

Fake-native tests cover argv/stdin, metadata and tool events, arbitrary UTF-8 stream splits,
malformed messages, answer retention and bounded state. Live installation and inference are
pending in the [verification queue](live-verification.md).

- [Adapter](../../src/pratfall/adapters/kimi.py)
- [Tests](../../tests/test_kimi.py)
