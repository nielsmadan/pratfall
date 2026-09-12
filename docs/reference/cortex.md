# Cortex Code / CoCo 1.1.78 baseline

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The [CLI reference](https://docs.snowflake.com/en/user-guide/cortex-code/cli-reference), checked
2026-09-11, establishes executable `cortex`, invocation `exec --file -`, and plain stdin.
Global `--model`, `--effort minimal|low|medium|high|max`, and `--max-turns` map the corresponding
Prat options; native turns count each conversation round. Optional `--connection`/`-c` takes one
value selecting an existing Snowflake connection. Global options precede `exec`. No fast or
spending controls are verified. Prompt/print/file/output, model/effort/turns, workdir/config,
session/resume/continue/private, plan/bypass, cloud/remote/executor and administrative controls
are reserved or rejected. `--github` implies cloud execution and is also blocked. The version
diagnostic is `--version`.

Exec disables plan mode and rejects interactive asks. Existing Snowflake account, connection and
[native authentication and policies](https://docs.snowflake.com/en/user-guide/cortex-code/security)
are prerequisites. Native startup can update its own installation/settings; Prat performs no setup
or configuration changes. JSONL framing/schema is unverified, so output is bounded complete native
stdout with terminal CR/LF removed, including any banners/progress. Native status determines
success; empty output can succeed, prose failures are not guessed, and no accounting is inferred.
These contracts were frozen before fixtures, without native execution.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/cortex.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
