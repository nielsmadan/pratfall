# Native agent interfaces

This reference records the external CLI contracts used by Pratfall. Each agent has its own
page so its source links, version baseline, and open questions stay together.

## Evidence scope

Initial interface evidence was recorded on 2026-09-09 through source inspection and
installed help, with live observations identified in the individual sections. Accounting
and fast-mode additions were recorded on 2026-09-10 through primary-source review and
fake-native tests. The twelve later adapters use the static 2026-09-11 baselines below. Grok Build
was added from official documentation and source inspection on 2026-09-14.
[Pi](pi.md) uses the v0.85.1 source baseline verified on 2026-09-18.
These dates describe the original evidence, not fresh compatibility checks. Native releases
can change the interfaces. Recheck the relevant agent page before updating its contract.

## Shared controls and accounting

Fast mode and version probes were established on 2026-09-10 through primary-source review and
fake-executable tests, not new live native runs. Codex 0.153.4's
[configuration schema](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/config.schema.json)
accepts `service_tier` strings; Prat maps true to the invocation override
`-c service_tier="priority"` and false to `-c service_tier="default"`. Claude's
[fast-mode guide](https://code.claude.com/docs/en/fast-mode#toggle-fast-mode) documents
noninteractive `--settings '{"fastMode": true}'`, and its
[CLI reference](https://code.claude.com/docs/en/cli-reference) says inline
settings apply to that session; Prat passes the same single-key object with either boolean. Omission
adds no override. Native account and model restrictions still apply, and settings files are never
changed.

Catalog entries use `--version` except Amp, whose diagnostic is the `version` subcommand. The
configured prefix is kept literal. Prat treats the stripped stdout, or stderr when stdout is empty,
as opaque strict UTF-8 and does not infer authentication. A nonzero exit, empty selected output,
invalid selected encoding, timeout, or output overflow becomes a per-agent `version_error`. The
default doctor path performs discovery only.

Native accounting mappings were established on 2026-09-10 through the linked primary sources and
fake-executable decoder tests, not new live native runs. Prat exposes only observed model
identifiers and native USD cost fields described below. Missing data remains null; requested models,
published prices, and non-USD credits are not used as substitutes.

Native agent selection was verified on 2026-09-18 for [Claude](claude.md#native-agent-selection-verified-2026-09-18),
[Copilot](copilot.md#native-agent-selection-verified-2026-09-18), and
[Vibe](vibe.md#native-agent-selection-verified-2026-09-18), using `--agent=NAME`. The selected
persona retains its native permission behavior; Vibe includes an auto-approve persona.
[OpenCode](opencode.md#native-agent-selection-verified-2026-09-18) is excluded because unknown
or subagent-only names fall back to its default. These checks used primary sources and fake
argv fixtures without authenticated execution.

## Interface comparison

| Agent | Native invocation | Model | Effort | Native limits | Evidence |
| --- | --- | --- | --- | --- | --- |
| [Claude Code](claude.md) | `claude -p --output-format json` | `--model` | `--effort` | `--max-budget-usd`, `--max-turns` | Installed 2.1.266, [CLI reference](https://code.claude.com/docs/en/cli-reference), and [Agent SDK result type](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage) |
| [Codex](codex.md) | `codex exec --json` | `--model` | `-c model_reasoning_effort="LEVEL"` | No verified total-token/spend cap | Installed 0.153.4, [noninteractive docs](https://developers.openai.com/codex/noninteractive), [event source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs) |
| [Gemini](gemini.md) | `gemini --output-format json --prompt=PROMPT` | `--model` | Unsupported | None verified | [Headless reference](https://geminicli.com/docs/cli/headless) and [UI telemetry source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts) |
| [Antigravity](antigravity.md) | `agy --input-format stream-json --output-format stream-json` with one stdin user event; requires 1.1.15+ | `--model` | `--effort low\|medium\|high` | No verified native timeout for stdin stream mode | [1.1.15 changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md) and [stdin stream docs](https://antigravity.google/docs/cli/headless#stream-prompts-from-stdin); installed 1.1.11 predates this interface |
| [Copilot](copilot.md) | `copilot --output-format=json --prompt=PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | `--max-ai-credits`, soft per-response cap | [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) and published 1.0.83 package source |
| [Kiro](kiro.md) | `kiro-cli chat --no-interactive --wrap never -- PROMPT` | `--model` | `--effort low\|medium\|high\|xhigh\|max` | None verified | [CLI reference](https://kiro.dev/docs/reference/cli-commands/), [headless](https://kiro.dev/docs/cli/headless/), and statically inspected 2.21.2 package |
| [Cursor](cursor.md) | `agent --print --output-format json agent -- PROMPT` | `--model` | Unsupported | None verified | [Parameters](https://cursor.com/docs/cli/reference/parameters), [output format](https://cursor.com/docs/cli/reference/output-format), and published 2026.09.08 package source |
| [OpenClaw](openclaw.md) | `openclaw agent exec --json --message-file -` | `--model provider/model` | `--thinking` | Native timeout in seconds | [Agent exec](https://docs.openclaw.ai/cli/agent) and [result projection](https://github.com/openclaw/openclaw/blob/main/src/commands/agent-exec-result.ts) |
| [Hermes](hermes.md) | `hermes chat --oneshot --quiet --query-file -` | `--model`, `--provider` | `--reasoning none\|minimal\|low\|medium\|high\|xhigh\|max\|ultra` | `--max-turns` | [CLI docs](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [source](https://github.com/NousResearch/hermes-agent/blob/main/cli.py) |
| [OpenCode](opencode.md) | `opencode run --format json` with stdin prompt | `--model provider/model` | `--variant` (provider-specific string) | None verified | Installed 1.18.29 help, [run emitter](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts), and [session processor](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts) |
| [OpenHands](openhands.md) | `openhands --headless --json --task=PROMPT` | Unsupported | Unsupported | None verified | [CLI 1.16.0 / SDK 1.21.0](openhands.md) |
| [Warp](warp.md) | `oz agent run --output-format ndjson --prompt=PROMPT` | `--model` | Unsupported | None verified | [Legacy Oz source](warp.md) |
| [iFlow](iflow.md) | `iflow --prompt=PROMPT` | `--model` | Unsupported | None verified | [0.5.19 package](iflow.md) |
| [Qwen Code](qwen.md) | `qwen --output-format stream-json` with stdin | `--model` | Unsupported | `--max-session-turns` | [0.23.3 source](qwen.md) |
| [Amp](amp.md) | `amp --execute --stream-json` with stdin | Unsupported | Unsupported | None verified | [Official docs](amp.md) |
| [Reasonix](reasonix.md) | `reasonix run --output-format json` with stdin | `--model` configured provider | `--effort` | Native `--max-steps`, not normalized turns | [1.38.5 source](reasonix.md) |
| [Droid](droid.md) | `droid exec --output-format json` with stdin | `--model` | `--reasoning-effort` | None verified | [0.209.0 baseline](droid.md) |
| [Kimi CLI](kimi.md) | `kimi --print --input-format text --output-format stream-json --final-message-only` with stdin | `--model` | Unsupported | None verified | [1.50.0 source](kimi.md) |
| [Mistral Vibe](vibe.md) | `vibe --prompt --output json` with stdin | Unsupported | Unsupported | `--max-turns`, `--max-price` | [2.25.2 source](vibe.md) |
| [Crush](crush.md) | `crush run --quiet` with stdin | `--model` | Unsupported | None verified | [0.93.1 source](crush.md) |
| [Devin](devin.md) | `devin -p -- PROMPT` | `--model` | Unsupported | None verified | [3000.10.21 baseline](devin.md) |
| [Cortex Code / CoCo](cortex.md) | `cortex exec --file -` with stdin | `--model` | `--effort minimal\|low\|medium\|high\|max` | `--max-turns` | [1.1.78 baseline](cortex.md) |
| [Grok Build](grok.md) | `grok --no-auto-update --output-format json --single=PROMPT` | `--model` | `--reasoning-effort none\|minimal\|low\|medium\|high\|xhigh\|max` | `--max-turns` | [Official docs and source](grok.md) |
| [Pi](pi.md) | `pi --print --mode json` with stdin | `--model` | `--thinking off\|minimal\|low\|medium\|high\|xhigh\|max` | None verified | [v0.85.1 source](pi.md) |

## Remaining factual verification

- Kiro authentic JSONL schema remains unavailable; the adapter intentionally uses documented text
  output with the limitation in [Kiro](kiro.md).
- Most agents were absent in the original setup. Fixture coverage does not establish live compatibility.

## Source acquisition notes for implementers

Use `gh` to read GitHub sources. `jina-fetch` caches docs and prints their exact paths; extract
short verbatim anchors from those cached files when schema text matters. Never use a summarizer's
reconstructed command as sole evidence for exact argv.

## A-tier contracts frozen on 2026-09-11

The OpenHands, Warp, iFlow, Qwen, Amp, Reasonix, Droid, Kimi, Vibe, Crush, Devin, and Cortex
contracts were established by static primary-source and package inspection before
writing their adapters and independent fake-native fixtures. No native agent, including help or
version, was executed. Fixtures establish Prat's behavior against these contracts, not compatibility
with every installed release. Version probes use `--version` except Amp's `version`; normal
doctor still only locates executables. Accounting is normalized only where the individual
agent pages establish a mapping; missing fields remain null.

The Grok contract was established separately on 2026-09-14 from official xAI documentation and
source revision `37949780c144e37df692e3d669051a21fec24f20`. Grok was not installed or executed.
