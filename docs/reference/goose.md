# Goose

**Evidence recorded:** 2026-10-13, current official source/docs; latest observed release v1.54.0.
No authenticated run yet.

## Native contract

The [CLI](https://github.com/aaif-goose/goose/blob/main/crates/goose-cli/src/cli.rs) and
[command documentation](https://goose-docs.ai/docs/guides/goose-cli-commands) establish
`goose run --quiet --no-session --output-format json --instructions -`, with the prompt on stdin.
Prat passes `--model` and `--max-turns`; native `--provider NAME` is available after `--`.
Model/provider overrides apply to this run. No effort, fast or spend-cap mapping is established.
Quiet suppresses nonresponse output; `--no-session` selects ephemeral native execution.

The [session implementation](https://github.com/aaif-goose/goose/blob/main/crates/goose-cli/src/session/mod.rs)
emits one JSON object containing `messages` and `metadata`. Prat requires a message array and
`metadata.status == "completed"`. Status `error` is a provider failure even if a native process
returns zero; the current implementation also propagates terminal errors to a nonzero exit.
Missing or unknown completion states fail closed. Native exit status remains authoritative.

In a local v1.54.0 startup attempt, a missing `OPENROUTER_API_KEY` produced plain text beginning
`error:` on stdout instead of JSON. The adapter preserves that diagnostic as a provider error;
on a nonzero exit, normalization includes it in the `native_exit` message. This attempt did not
reach inference or verify successful authenticated output.

The [message schema](https://github.com/aaif-goose/goose/blob/main/crates/goose-provider-types/src/conversation/message.rs)
uses `role` and a content array. Prat selects the last assistant message containing text, joining
its `type: text` blocks with blank lines. User, tool and reasoning content do not become the
answer. Empty assistant messages do not erase earlier text. This is a projection of native
history, not a dedicated final-answer field. Whole-document size and JSON validation limits apply.

Optional native `metadata.cost_usd` is reported when finite and nonnegative. Token metadata exists,
but its provider-specific input/cache inclusion semantics were not fully established in this
audit; usage remains null. Reported models and session ID are also unknown in this envelope.

Native configuration and permissions remain active. Run `goose configure` before headless use;
Prat performs no setup. [Install](https://goose-docs.ai/docs/getting-started/installation) with
`brew install block-goose-cli` in your terminal.

- [Adapter](../../src/pratfall/adapters/goose.py)
- [Tests](../../tests/test_goose.py)
- [Installation and verification queue](live-verification.md)
