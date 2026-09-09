# Agents and limitations

Run `prat agents` for current aliases and supported settings. Model identifiers are passed through
when the native CLI supports model selection; Pratfall does not freeze a model catalog.

| Agent | Aliases | Output and usage notes |
| --- | --- | --- |
| Claude Code | `cc` | Structured final result and token usage |
| Codex | `cx` | Structured event stream and token usage |
| Gemini | `gm` | Structured final result and token usage; no effort override |
| Antigravity | `ag`, `agy` | Structured stream and cumulative usage |
| Copilot | `cp` | Structured final text; no token counts |
| Kiro | `ki` | Text mode; usage unknown |
| Cursor | `cu` | Validated JSON result envelope; usage unknown; no effort override |
| OpenClaw | `claw` | Embedded `agent exec`; optional token usage |
| Hermes | `hm` | Quiet text mode; usage unknown |
| OpenCode | `oc` | Structured stream and token usage |

Kiro's invocation-scoped `--model` and `--` delimiter behavior are supported from static inspection
of the official 2.21.2 package and its Clap 4.5.60 parser. Kiro was not live-tested for this claim.
Its text mode can include banners or progress on stdout. Hermes is also text-only in the selected
quiet one-shot path.

There is no common verified hard token cap. Claude exposes native USD and turn budgets. Copilot's
`max_ai_credits` is a soft per-response limit and may not stop exactly at the requested amount.
Hermes supports a native turn count. Pratfall forwards those native controls without strengthening
their guarantees.
