# Agents and limitations

Every agent accepts the name in the **Long form** column, for example `prat claude "fix this bug"`
or `prat codex "review this change"`. Longer names also have aliases, such as `prat cc "fix this bug"`.
Names of four letters or fewer use the name itself. Run `prat agents` for the current selectors and
supported settings. Model identifiers are passed through when the native CLI supports model
selection; Pratfall does not freeze a model catalog.

- [Selectors](#selectors)
- [Shared controls and limitations](#shared-controls-and-limitations)
- [Native behavior](#native-behavior)

## Selectors

| Agent | Long form | Aliases | Output, usage, and accounting notes |
| --- | --- | --- | --- |
| Claude Code | `claude` | `cc` | Structured result, token usage, modelUsage IDs, native USD cost; invocation-only fast override |
| Codex | `codex` | `cx` | Structured event stream and token usage; invocation-only fast override |
| Gemini | `gemini` | `gm` | Structured result, token usage, and stats model IDs; no native cost or effort override |
| Antigravity | `antigravity` | `ag`, `agy` | Structured stream and cumulative usage; stdin mode requires 1.1.15+ |
| Copilot | `copilot` | `cp` | Structured final text and root-message model IDs; no token counts or USD cost |
| Kiro | `kiro` | none | Text mode; usage unknown |
| Cursor | `cursor` | `cu` | Validated JSON result envelope; usage unknown; no effort override |
| OpenClaw | `openclaw` | `claw` | Embedded `agent exec`; optional token usage, model identity, and native USD cost |
| Hermes | `hermes` | `hm` | Quiet text mode; usage unknown |
| OpenCode | `opencode` | `oc` | Structured stream, token usage, and per-step native USD cost; model IDs unavailable |
| OpenHands | `openhands` | `oh` | SDK final assistant/finish text; native headless autoapproval; no model/effort override; accounting unknown |
| Warp | `warp` | none | Legacy executable `oz`; NDJSON agent text; model override, no effort or accounting |
| iFlow | `iflow` | `if` | Legacy text mode; existing BYOK setup required; model override, no effort or accounting |
| Qwen Code | `qwen` | none | JSONL final result, token counts and root-message model IDs; model and native session-turn limit |
| Amp | `amp` | none | JSONL final result and optional token counts; no generic model, effort or budget override |
| Reasonix | `reasonix` | `rx` | Whole JSON final result and native token counts; configured provider selector and provider-specific effort |
| Droid | `droid` | `dr` | Whole JSON result; model and provider-specific reasoning effort; accounting unknown |
| Kimi CLI | `kimi` | none | Final-only assistant JSONL; native autoapproval; model override; accounting unknown |
| Mistral Vibe | `vibe` | none | Whole public-history JSON; native turns/USD limits; no model/effort override or accounting |
| Crush | `crush` | `cr` | Quiet text capture; local run autoapproval; model override; accounting unknown |
| Devin | `devin` | `dv` | Print text capture; requires trusted workspace; model override; accounting unknown |
| Cortex Code / CoCo | `cortex` | `co` | Exec text capture; Snowflake connection required; model/effort/turns; accounting unknown |
| Grok Build | `grok` | none | Whole JSON result; model/effort/turns; native tokens, model IDs and complete USD cost |

## Shared controls and limitations

Kiro's invocation-scoped `--model` and `--` delimiter behavior are supported from static inspection
of the official 2.21.2 package and its Clap 4.5.60 parser. Kiro was not live-tested for this claim.
Its text mode can include banners or progress on stdout. Hermes is also text-only in the selected
quiet one-shot path.

There is no common verified hard token cap. Claude and Vibe expose native USD and turn budgets. Copilot's
`max_ai_credits` is a soft per-response limit and may not stop exactly at the requested amount.
Hermes, Qwen, Cortex and Grok support native turn counts. Pratfall forwards those native controls without strengthening
their guarantees.

Claude fast mode is passed as an inline `fastMode` setting for the current invocation. Codex fast
mode is passed as `service_tier="priority"`; false selects `service_tier="default"`. Pratfall does
not edit native settings, choose a different model, or infer account eligibility or pricing.

Antigravity's documented `--print-timeout` examples apply to print mode. Its stdin-stream timeout
contract is not verified, so Pratfall relies on its configured outer `--timeout` and strict terminal
event checks instead of forwarding the print-only option.

## Native behavior

### OpenHands

OpenHands support targets CLI 1.16.0 with its pinned SDK 1.21.0. It requires native setup before
running headlessly. This mode automatically approves actions and disables the native critic.
Prat does not provide an approval layer. Set the model in native settings, or explicitly pass
`--override-with-envs` through native arguments to select an existing environment configuration.
The adapter requires a terminal SDK assistant message or finish action; native status lines and
the echoed conversation summary do not prove completion. Conversation error events remain
failures even if the native CLI exits zero. A finish message may explain that the agent could not
perform the requested work; Prat does not infer task success from its prose.

### Warp

Warp uses local `oz agent run`. Its [legacy `oz` interface](https://docs.warp.dev/reference/cli/) is documented through the end of
September 2026, and the newer `warp` TUI has no verified replacement one-shot invocation here.
Compatibility evidence must be rechecked at release time; Prat has no date cutoff or automatic
fallback. NDJSON agent messages are joined in order, excluding reasoning and tool output. Tool
errors can be recovered from; successful native exit and EOF complete the stream, including runs
with no text. Native permissions and authentication remain active. Optional native arguments are
`--name`/`-n`, `--strict-mcp-startup`, and `--mcp-startup-timeout`.

### iFlow

iFlow support targets the published 0.5.19 package. Maintenance ended on 2026-03-20 and its hosted
service closed on 2026-04-17. The [official FAQ](https://vibex.iflow.cn/t/topic/4819) confirms existing installations can continue
using custom APIs; configure BYOK natively before using this adapter. The pinned package defaults to
automatic approval for noninteractive prompts unless native settings or explicit modes override
it. Native `--default` (manual approval), `--plan` (planning), and `--thinking` are available after
Prat's `--` separator, for example `prat if "review this" -- --plan`. Thinking is not a generic
effort setting. Captured stdout may include banners/progress, and prose errors cannot be detected;
native exit status determines success. Usage, observed model and cost stay unknown.

OpenHands, Warp and iFlow inherit native authentication/settings. Native startup may itself update or
migrate settings; Prat does not run setup or edit vendor configuration. Their argv prompt
transports preserve literal data while remaining subject to OS argument-size limits.

### Qwen Code

Qwen support targets 0.23.3 with plain stdin and stream-json output. The final valid native result
at EOF determines completion: intermediate subagent error results can be followed by root success.
Root assistant text and models exclude child messages; final usage replaces message snapshots.
Qwen denies unresolved interactive approvals. Optional native flags are `--debug`/`-d`,
`--approval-mode`, `--system-prompt`, and `--append-system-prompt`. No generic effort or spend cap
is verified; `max_turns` maps to `--max-session-turns` with native counting semantics.

### Amp

Amp uses `--execute --stream-json` with plain stdin and one terminal result. Native error strings
and system errors remain failures; final result usage is optional. Native mode does not identify
a model, so model/effort/fast/budget overrides are unsupported. `--stream-json-thinking` is the
only optional native argument; thinking remains excluded from final output. Current Amp defaults
to automatic tool approval unless existing native settings enable permissions. Prat preserves
that behavior. Its version diagnostic is `amp version`.

### Reasonix

Reasonix support targets 1.38.5. `--model` selects a configured provider name; effort levels depend
on that provider. Native `--max-steps` counts tool-call rounds and is available after `--`, alongside
`--permission-mode` and `--show-thinking`. It is not mapped to `max_turns`. Default headless ask
permissions fail closed. Native stdin trims surrounding whitespace. Only a success result completes
successfully: recovery_paused remains unfinished even with native exit zero. Input/output and
cache-read counts are preserved, while cache-write counts and USD cost remain unknown because
the native cache-creation and USD aliases have different meanings. These three adapters inherit
existing authentication/settings and perform no setup.

### Droid

Droid uses `exec --output-format json` with plain stdin and requires a successful native result
object. It defaults to read-only. Explicit `--auto low|medium|high` is available after `--`, but
Prat never injects it. Model and reasoning effort are supported; the accepted effort union is
`none|dynamic|off|minimal|low|medium|high|xhigh|max`, with each model accepting its own subset.
Custom models control reasoning in their native provider settings. Usage, observed models and
USD cost remain unknown.

### Kimi CLI

Kimi support targets kimi-cli 1.50.0, not its successor kimi-code. Print mode automatically approves
tools and dismisses user questions. Prat uses final-message-only stream JSON with plain stdin,
which native Kimi trims. The last emitted assistant string is returned; exit zero and EOF can
succeed with no output. Native buffered text cannot be recovered if never emitted. Native
`--thinking`/`--no-thinking`, `--plan`, and `--debug` are available after `--`; thinking is boolean,
not generic effort. There is no verified accounting. Plain-text native errors still preserve the
native failure status and any answer already emitted.

### Mistral Vibe

Mistral Vibe support targets 2.25.2. Bare `--prompt --output json` reads and trims stdin, then
returns complete public history. Prat selects the last nonempty assistant message and joins text
blocks with blank lines. A valid array without assistant text can succeed with empty output;
reasoning, tool output and notices are excluded. Model and effort overrides are unsupported: use
native settings or a trusted command prefix such as `["env", "VIBE_ACTIVE_MODEL=local", "vibe"]`
for an already configured model. Default native permissions accept edits; headless approval
callbacks are denied. `max_turns` maps to `--max-turns` and `max_budget_usd` to `--max-price` in
dollars; native usage can exceed these limits before interruption. Optional `--max-tokens` counts
cumulative prompt plus completion tokens; it remains a native argument, alongside
`--enabled-tools` and `--disabled-tools`. Remote/teleport execution is blocked because it can
synchronize Git and changes the output protocol. Accounting remains unknown. All three adapters
inherit existing authentication and perform no native setup or configuration changes.

### Crush

Crush support targets 0.93.1. `run --quiet` reads stdin and hides the native spinner. Prat sends
the original prompt bytes; Crush adds two trailing newline characters without trimming the input.
Local run automatically approves its session. Existing provider setup is required. `CRUSH_CLIENT_SERVER`
can select a server backend with lifetime ownership outside Prat's child process group; native
model overrides there can update workspace preferences. Model selection is supported; generic
effort, fast and budgets are unsupported. Optional native flags are `--verbose`/`-v` and
`--debug`/`-d`.

### Devin

Devin support uses the 3000.10.21 baseline and documented `-p -- PROMPT` path. The prompt is one
literal argv item, so operating-system argv-size limits can apply below Prat's input limit.
Print requires existing authentication and an already trusted workspace; Prat adds no trust
bypass. Native `--permission-mode VALUE` may be explicitly selected after `--`; the exact outcome
of every headless tool approval is not established. Model selection is supported; effort, fast
and budgets are unsupported.

### Cortex Code

Cortex Code / CoCo support uses executable `cortex` and the 1.1.78 baseline. `exec --file -`
reads stdin. Existing Snowflake account, connection and native authentication/policies are
required; `--connection`/`-c` after Prat's separator selects an already configured connection.
Model selection, effort `minimal|low|medium|high|max`, and `max_turns` are supported; turns retain
native per-round counting. Exec disables plan mode and rejects interactive asks. Cloud controls,
including `--github` which implies cloud execution, are blocked. Native startup can update its
own installation/settings; Prat does not perform setup or change configuration.

All three return bounded complete native stdout with terminal CR/LF removed. Banners/progress
can appear in the text; there is no final-answer separation guarantee or prose failure detection.
Native exit status determines success, including empty output. Usage, reported models and USD
cost remain null. Their version diagnostic is `--version`; normal doctor does not execute it.

### Grok Build

Grok Build support uses executable `grok` and the official headless `--single` path. Prat selects
whole-document JSON, disables the invocation's auto-update check, and passes the prompt as one argv
item. Grok trims surrounding whitespace natively. Existing
xAI API-key or OAuth authentication is required.

Model selection, reasoning effort `none|minimal|low|medium|high|xhigh|max`, and `max_turns` are
supported; each model advertises its own effort subset. Native default headless permissions cancel
unresolved approval requests. Prat does not add `--always-approve`; explicit permission, tool,
agent, rules, sandbox, approval, structured-output and background-wait controls can be passed
after `--`.

Only `stopReason: end_turn` completes successfully. Other terminal reasons retain partial text as a
provider error. Prat maps native fresh input, cache-read, cache-creation, output and reasoning
tokens, `modelUsage` keys, and complete USD cost. Missing cost stays null. Incomplete usage is
reported as a protocol error instead of exact accounting. Native JSON error objects preserve their
message when Grok exits zero; a nonzero exit reports the native exit status instead. The version
diagnostic is `--version`; normal doctor does not execute it.
