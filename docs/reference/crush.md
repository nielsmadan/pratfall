# Crush 0.93.1

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The pinned [run command](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/run.go)
establishes `crush run --quiet` with plain stdin and optional `--model VALUE`. Quiet hides the
spinner. Prat sends the original UTF-8 bytes; native
[`MaybePrependStdin`](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/root.go#L918)
adds two newline characters before the empty positional prompt, and `run` forwards that combined
text without trimming. The independent fake records raw stdin separately from this native prompt.
Accepted native options are `--verbose`/`-v` and `--debug`/`-d`, each with zero values;
debug is inherited from the [root command](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/cmd/root.go).
Prompt/output/model/small-model, cwd/data-dir/config, session/continue, host/channels/server,
permission-bypass and administrative selectors are reserved or rejected. Effort, fast and
normalized budgets are unsupported. The version diagnostic is `--version`.

The [local application](https://github.com/charmbracelet/crush/blob/v0.93.1/internal/app/app.go)
automatically approves the noninteractive session. Existing provider configuration is required.
`CRUSH_CLIENT_SERVER` can select a server backend whose lifetime is not owned by Prat's child
process group; native model overrides there can update workspace preferences. Prat inherits
these native behaviors without enabling a server, adding approval flags or editing settings itself.
Output is bounded complete stdout with terminal CR/LF removed. Native exit status is authoritative;
empty text can succeed, banners/progress are preserved, and prose cannot establish failure.
Usage, reported models and USD cost remain null. Source verification on 2026-09-11 preceded fixtures.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/crush.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
