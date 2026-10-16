# Kilo

**Evidence recorded:** 2026-10-13, current official docs and source inspection. No authenticated run yet.

## Native contract

The [official CLI guide](https://kilo.ai/docs/code-with-ai/platforms/cli) states that Kilo is an
OpenCode fork. The [run source](https://github.com/Kilo-Org/kilocode/blob/main/packages/opencode/src/cli/cmd/run.ts)
uses `kilo run --format json` with stdin-only prompts. Prat executes the literal command prefix
with a child-only `KILO_NO_DAEMON=1` environment override to prevent automatic attachment to an
existing daemon. The runner preserves inherited environment variables and native executable
startup errors.
The [daemon client](https://github.com/Kilo-Org/kilocode/blob/main/packages/opencode/src/kilocode/daemon/client.ts)
checks that variable; without it, work can outlive the process group Prat owns. Executable discovery
still checks `kilo`, not `env`. No shell is involved and the parent environment is unchanged.

Public model, effort and attachments map to `--model provider/model`, `--variant VALUE`, and
repeated `--file=PATH`. Accepted native flags are `--auto`, `--thinking`, `--print-logs`, `--agent`,
`--title` and `--log-level`; the latter three take one value. No approval flag is injected.
Remote attachment, resume, sharing, cwd, prompt and output selectors are reserved or rejected.

The [schema](https://github.com/Kilo-Org/kilocode/blob/main/packages/schema/src/v1/session.ts)
and run emitter match the [OpenCode streaming contract](opencode.md): completed text parts,
step-finish reasons, native errors, per-step token counts and native USD cost. Only root-session
parts are emitted. Kilo and OpenCode share the bounded
[protocol consumer](../../src/pratfall/adapters/opencode_stream.py), including its OpenCode
protocol diagnostics. They retain separate builders and native argument allowlists.
Native nonzero exits and error records fail even after an answer; a stop step is required for
success. Usage snapshots replace the same step's earlier snapshot. Model metadata is not currently
normalized, and no session ID is reported by this shared consumer.

Install `npm install -g @kilocode/cli`, then authenticate with `kilo auth login` or interactive
`/connect`, following the [CLI reference](https://kilo.ai/docs/code-with-ai/platforms/cli-reference).
Native credentials and permission rules remain active.

- [Adapter](../../src/pratfall/adapters/kilo.py)
- [Tests](../../tests/test_kilo.py)
- [Installation and verification queue](live-verification.md)
