# Kiro

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Kiro 2.21.2 package, statically inspected.

## Native contract

Docs advertise `--output-format stream-json` on V2/V3, but omit event schemas. V3 documentation
also says classic `kiro-cli chat` does not support V3, so do not silently force an engine.
Native exit codes include 0 success, 1 failure, 3 mandatory MCP startup failure.
Do not confuse model-list `--format json` with chat format or build a structured decoder from
convenient guessed fixtures.

Static inspection of the [official Kiro CLI 2.21.2 archive](https://prod.download.cli.kiro.dev/stable/2.21.2/kirocli-aarch64-linux.zip), whose checksum matched the [official stable manifest](https://prod.download.cli.kiro.dev/stable/latest/manifest.json), verifies that the native chat arguments include invocation-scoped `--model`. The archive identifies Clap 4.5.60; its [matching parser source](https://github.com/clap-rs/clap/blob/v4.5.60/clap_builder/src/parser/parser.rs) switches to positional-only parsing after `--`, establishing the end-of-options behavior used here. This was static package and parser verification, not a live Kiro execution.

Prat uses the documented noninteractive text response with wrapping disabled and protects the
positional prompt with the native end-of-options marker. It returns the complete stdout text,
apart from terminal line endings, because the docs do not guarantee that banners and tool progress
are separated from the final answer. Usage stays null. The prompt travels in argv and inherits the
operating system's lower size limit. Current headless mode requires `KIRO_API_KEY`; Prat inherits
native authentication and does not inspect or modify it. The documented reasoning enum is passed
through `--effort`; the resolved model is passed through `--model`.

## Sources

- [CLI reference](https://kiro.dev/docs/reference/cli-commands/)
- [headless](https://kiro.dev/docs/cli/headless/)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/kiro.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
