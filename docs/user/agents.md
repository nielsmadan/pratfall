# Agents and limitations

Use an agent name or alias to choose which CLI runs your prompt:

```sh
prat claude "fix this bug"
prat cc "fix this bug"
```

Both commands run Claude Code. Run `prat agents` to list available names, aliases and supported
settings. Where model selection is supported, Prat passes model identifiers to the native CLI;
it does not maintain a fixed model catalog.

- [Selectors](#selectors)
- [Shared controls and limitations](#shared-controls-and-limitations)
- [Native behavior](#native-behavior)

## Selectors

| Agent | Name | Aliases | Output, usage and limits |
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
| Pi | `pi` | none | v0.85.1+ JSON events; model/effort/tools/attachments; native session saving |

## Shared controls and limitations

### Text output

Kiro's invocation-scoped `--model` and `--` delimiter behavior are supported from static inspection
of the official 2.21.2 package and its Clap 4.5.60 parser. Kiro was not live-tested for this claim.
Its text mode can include banners or progress on stdout. Hermes is also text-only in the selected
quiet one-shot path.

### Schema output

`--schema PATH` / `schema` is supported by these agents:

| Agent | Native transport | Authoritative answer |
| --- | --- | --- |
| Claude | `--json-schema JSON` | Terminal `structured_output`. |
| Codex | `--output-schema FILE` | Last completed `agent_message`, with `turn.completed`. |
| Qwen 0.24+ | `--json-schema @FILE` | Terminal success `structured_result`. |

Prat emits compact JSON as `output` and the parsed value as `structured_output`. Native dialect
validation remains authoritative; Qwen roots must accept objects. No implicit version probe runs.
See [schema output rules](running.md#request-schema-output) and the verified contracts for
[Claude](../reference/claude.md#schema-output-verified-2026-09-18),
[Codex](../reference/codex.md#schema-output-verified-2026-09-18) and
[Qwen](../reference/qwen.md#schema-output-verified-2026-09-18).

### Budgets

There is no verified hard token cap shared by all agents. Claude and Vibe expose native USD and
turn budgets. Copilot's `max_ai_credits` is a soft per-response limit and may not stop exactly at
the requested amount. Hermes, Qwen, Cortex and Grok support native turn counts. Prat forwards these controls with their
native guarantees.

### Fast mode

Claude fast mode is passed as an inline `fastMode` setting for the current invocation. Codex fast
mode is passed as `service_tier="priority"`; false selects `service_tier="default"`. Pratfall does
not edit native settings, choose a different model, or infer account eligibility or pricing.

### Extra directories

| Agent | Native mapping | Native scope |
| --- | --- | --- |
| Claude Code | `--add-dir PATH` | Additional directories for file access. |
| Codex | `--add-dir PATH` | Additional writable roots alongside the workspace. |
| Gemini | `--include-directories PATH` | Additional workspace directories. |
| Qwen Code | `--include-directories PATH` | Additional workspace directories. |
| Copilot | `--add-dir=PATH` | Additional entries in the allowed-paths list. |

`--add-dir` / `add_dirs` is unsupported for other agents unless the configured list is empty.
These settings retain each agent's native permissions and tool approvals; Prat adds no automatic
approval flags. Gemini and Qwen split commas and trim each value, so Prat rejects directory names
that would change during parsing. Directory availability does not prepend files to the prompt;
use `--context` for that behavior.

### Appended instructions

| Agent | Native mapping |
| --- | --- |
| Claude Code | `--append-system-prompt=TEXT` |
| Codex | `-c developer_instructions=TOML_STRING` |
| Qwen Code | `--append-system-prompt=TEXT` |
| Droid | `--append-system-prompt=TEXT` |

Other agents reject both `instructions` and `instructions_file`. Built-in guidance remains in
place; Codex's value replaces an existing native configured `developer_instructions` value.
Prat reads file content itself and forwards validated text through the same mapping. Native
argument-size limits still apply. See [instruction inputs](running.md#append-instructions).

### Antigravity timeouts

Antigravity's documented `--print-timeout` examples apply to print mode. Its stdin-stream timeout
contract is not verified, so Pratfall relies on its configured outer `--timeout` and strict terminal
event checks instead of forwarding the print-only option.

Tool availability controls are supported by Claude, Qwen, Copilot, Droid, Vibe and Pi. Their
[native scopes and empty-list behavior](running.md#control-tool-availability) differ; inventory
JSON includes support booleans, scope descriptions and `tools_empty`. These are native tool
filters, not an OS sandbox or a permission approval mechanism.

## Native behavior

### Pi

Requires Pi v0.85.1 or newer. `prat pi "review this checkout"` uses `pi --print --mode json`
with stdin. Pi trims prompt-edge whitespace and saves its native session by default; use
`prat pi "review" -- --no-session` for an ephemeral native session.

Use `--model provider/model`, `--effort high`, `--tools read`, `--disable-tools bash`, or
`--attach screenshot.png`. Effort accepts `off`, `minimal`, `low`, `medium`, `high`, `xhigh`,
and `max`; actual support depends on the native model. Tool lists cover built-in, extension
and custom tools, including an empty config allowlist (`tools = []`).

Prat returns the latest completed assistant text after Pi settles its native retries and
compaction. Provider failures are detected even when Pi exits zero. Usage and USD cost cover
emitted assistant messages; hidden compaction or extension inference is not included.

Public `--instructions` is unsupported: Pi interprets values matching existing files as filenames.
Use native `--append-system-prompt` after `--` when those semantics are intended. Public schemas,
extra directories, native-agent selection, fast mode and budgets are unsupported. See the
[Pi reference](../reference/pi.md) for supported passthrough flags, path restrictions and evidence.


### OpenHands

Support targets CLI 1.16.0 with its pinned SDK 1.21.0.

- **Setup and permissions:** configure OpenHands before running headlessly. Headless mode
  automatically approves actions and disables the native critic. Prat adds no approval layer.
- **Model:** use native settings, or pass `--override-with-envs` after `--` to select an existing
  environment configuration.
- **Completion:** Prat requires a terminal SDK assistant message or finish action. Status lines
  and the echoed conversation summary do not prove completion. Conversation error events fail
  even with exit zero. A finish message can report that the work could not be done; Prat does
  not judge task success from its prose.

### Warp

Warp uses local `oz agent run`. Its
[legacy `oz` interface](https://docs.warp.dev/reference/cli/) is documented through the end of
September 2026. The newer `warp` TUI has no verified replacement one-shot invocation here.
Compatibility needs rechecking at release time; Prat has no date cutoff or automatic fallback.

- **Permissions:** native permissions and authentication remain active.
- **Native arguments:** `--name`/`-n`, `--strict-mcp-startup` and `--mcp-startup-timeout`.
- **Output:** Prat joins NDJSON agent messages in order, excluding reasoning and tool output.
  Tool errors can be recovered from. Exit zero and end of stream complete the run, even without text.

### iFlow

iFlow support targets the published 0.5.19 package. Maintenance ended on 2026-03-20 and its hosted
service closed on 2026-04-17. The [official FAQ](https://vibex.iflow.cn/t/topic/4819) confirms
existing installations can continue using custom APIs. Configure your own API key natively before
using this adapter. The pinned package defaults to automatic approval for noninteractive prompts unless native settings or
explicit modes override it.

- **Native arguments:** `--default` (manual approval), `--plan` (planning) and `--thinking`, for
  example `prat if "review this" -- --plan`. Thinking is not a generic effort setting.
- **Output:** stdout may include banners and progress. Prat cannot detect errors in prose;
  native exit status determines success. Usage, observed model and cost stay unknown.

**Shared by OpenHands, Warp and iFlow:** they inherit native authentication and settings. Native
startup may update or migrate settings; Prat does not run setup or edit vendor configuration.
Prompts are passed as literal arguments, subject to operating-system argument-size limits.

### Qwen Code

Support targets 0.23.3 with plain stdin and stream-json output.

- **Permissions:** Qwen denies unresolved interactive approvals.
- **Controls:** `max_turns` maps to `--max-session-turns` with native counting semantics. No
  generic effort or spend cap is verified. Native flags include `--debug`/`-d`, `--approval-mode`,
  `--system-prompt`, `--append-system-prompt`, `--include-directories` and its `--add-dir` alias.
- **Output:** the final valid result at end of stream determines completion. An intermediate
  subagent error can be followed by root success. Root assistant text and models exclude child
  messages; final usage replaces message snapshots.

### Amp

Amp uses `--execute --stream-json` with plain stdin and one terminal result.

- **Permissions:** Amp defaults to automatic tool approval unless existing native settings enable
  permissions. Prat preserves that behavior.
- **Controls:** model, effort, fast and budget overrides are unsupported. The only optional native
  argument is `--stream-json-thinking`; thinking stays excluded from final output.
- **Output:** native error strings and system errors remain failures. Usage is optional, and
  native mode does not identify a model. The version diagnostic is `amp version`.

### Reasonix

Support targets 1.38.5.

- **Permissions:** default headless ask permissions fail closed.
- **Controls:** `--model` selects a configured provider name; effort levels depend on the provider.
  Native `--max-steps` counts tool-call rounds and is available after `--`, alongside
  `--permission-mode` and `--show-thinking`. It does not map to `max_turns`.
- **Input and output:** native stdin trims surrounding whitespace. Only a success result completes
  successfully; `recovery_paused` is unfinished even with exit zero. Input, output and cache-read
  counts are preserved. Cache-write counts and USD cost stay unknown because native cache-creation
  and USD aliases have different meanings.

**Shared by Qwen, Amp and Reasonix:** they inherit existing authentication and settings and perform
no setup.

### Droid

Droid uses `exec --output-format json` with plain stdin.

- **Permissions:** read-only by default. You can pass `--auto low|medium|high` after `--`; Prat
  never adds it automatically.
- **Controls:** model and reasoning effort are supported. Effort values are
  `none|dynamic|off|minimal|low|medium|high|xhigh|max`; each model accepts its own subset. Custom
  models control reasoning through native provider settings.
- **Output:** Prat requires a successful native result object. Usage, observed models and USD
  cost stay unknown.

### Kimi CLI

Support targets kimi-cli 1.50.0, not its successor kimi-code.

- **Permissions:** print mode automatically approves tools and dismisses user questions.
- **Controls:** native `--thinking`/`--no-thinking`, `--plan` and `--debug` are available after
  `--`. Thinking is boolean, not generic effort.
- **Input and output:** Prat uses final-message-only stream JSON with plain stdin, which Kimi
  trims. It returns the last emitted assistant string; exit zero and end of stream can succeed
  without output. Buffered text cannot be recovered if Kimi never emits it. Plain-text native
  errors preserve the failure status and any answer already emitted. Accounting is unverified.

### Mistral Vibe

Support targets 2.25.2. Bare `--prompt --output json` reads and trims stdin, then returns the
complete public history.

- **Permissions:** native defaults accept edits; headless approval callbacks are denied.
  Remote/teleport execution is blocked because it can synchronize Git and changes the output protocol.
- **Model:** use native settings or a trusted command prefix such as
  `["env", "VIBE_ACTIVE_MODEL=local", "vibe"]` for an already configured model. Generic model and
  effort overrides are unsupported.
- **Limits:** `max_turns` maps to `--max-turns`; `max_budget_usd` maps to `--max-price` in dollars.
  Native usage can exceed these limits before interruption. Native `--max-tokens` counts cumulative
  prompt plus completion tokens. It is available after `--`, alongside `--enabled-tools` and
  `--disabled-tools`.
- **Output:** Prat selects the last nonempty assistant message and joins text blocks with blank
  lines, excluding reasoning, tool output and notices. A valid array without assistant text can
  succeed with empty output. Accounting stays unknown.

**Shared by Droid, Kimi and Vibe:** they inherit existing authentication and perform no native
setup or configuration changes.

### Crush

Support targets 0.93.1. `run --quiet` reads stdin and hides the native spinner. Prat sends the
original prompt bytes; Crush adds two trailing newlines without trimming the input.

- **Setup and permissions:** existing provider setup is required. Local run automatically
  approves its session.
- **Controls:** model selection is supported; generic effort, fast and budgets are unsupported.
  Optional native flags are `--verbose`/`-v` and `--debug`/`-d`.
- **Server backend:** `CRUSH_CLIENT_SERVER` can select a server whose lifetime is outside Prat's
  child process group. Native model overrides there can update workspace preferences.

### Devin

Support uses the 3000.10.21 baseline and documented `-p -- PROMPT` path. The prompt is one literal
argument, so operating-system argument-size limits can apply below Prat's input limit.

- **Setup and permissions:** print mode requires existing authentication and an already trusted
  workspace. Prat adds no trust bypass. You can pass native `--permission-mode VALUE` after `--`;
  the outcome of every headless tool approval is not established.
- **Controls:** model selection is supported; effort, fast and budgets are unsupported.

### Cortex Code

Cortex Code / CoCo uses executable `cortex` and the 1.1.78 baseline. `exec --file -` reads stdin.

- **Setup:** an existing Snowflake account, connection and native authentication/policies are
  required. Pass `--connection`/`-c` after `--` to select an already configured connection.
  Native startup can update its installation and settings; Prat performs no setup or configuration
  changes.
- **Permissions:** exec disables plan mode and rejects interactive asks. Cloud controls are
  blocked, including `--github`, which implies cloud execution.
- **Controls:** model selection, effort `minimal|low|medium|high|max` and `max_turns` are supported.
  Turns retain native per-conversation-round counting.

**Shared by Crush, Devin and Cortex:** Prat returns bounded, complete stdout with trailing
carriage returns and newlines removed. Banners and progress may appear alongside the answer;
Prat cannot detect errors in prose. Native exit status determines success, including empty
output. Usage, reported models and USD cost stay null. Their version diagnostic is `--version`;
normal doctor does not execute it.

### Grok Build

Grok Build uses executable `grok` and the official headless `--single` path. Prat selects
whole-document JSON and disables the invocation's auto-update check.

- **Setup and input:** existing xAI API-key or OAuth authentication is required. Prat passes the
  prompt as one argument; Grok trims surrounding whitespace.
- **Permissions:** native headless defaults cancel unresolved approvals. Prat does not add
  `--always-approve`. Explicit permission, tool, agent, rules, sandbox, approval, structured-output
  and background-wait controls can be passed after `--`.
- **Controls:** model selection, effort `none|minimal|low|medium|high|xhigh|max` and `max_turns` are
  supported. Each model advertises its own effort subset. `max_turns` maps to Grok's native
  agentic-turn limit.
- **Completion:** only `stopReason: end_turn` succeeds. Other terminal reasons return a provider
  error with partial text. Native JSON errors preserve their message on exit zero; a nonzero exit
  reports the native exit status instead.
- **Accounting:** Prat maps native fresh input, cache-read, cache-creation, output and reasoning
  tokens, `modelUsage` keys and complete USD cost. Missing cost stays null. Incomplete usage is a
  protocol error rather than exact accounting.

The version diagnostic is `--version`; normal doctor does not execute it.

## Native agent selection

Claude Code, Copilot and Vibe support `--native-agent NAME` / `native_agent`, mapped to
`--agent=NAME`. `prat agents --json` exposes the boolean `native_agent` capability. Other agents
reject public selection, including OpenCode: its native CLI falls back to the default when a
name is unknown or identifies a subagent-only definition. Existing native-only OpenCode
`--agent` passthrough remains accepted.

This is a native persona within the selected backend, separate from Prat profiles and selectors.
Prat preserves literal names and leaves discovery, definitions and resolution to the native CLI.
Personas can change native permission behavior; Vibe's `auto-approve` persona can approve tool
calls automatically. Prat adds no approval flags. See [selection rules](running.md#select-a-native-agent).

## Native attachments

| Agent | Native mapping | Supported input |
| --- | --- | --- |
| Codex | Repeated `--image=PATH`, followed by `-- -` for the stdin task | Images; comma-free paths. |
| Hermes | `--image=PATH` | One image per query. |
| Copilot | Repeated `--attachment=PATH` | Images and native documents. |
| OpenCode | Repeated `--file=PATH` | Files; public attachments reject directories. |
| Pi | Repeated `@PATH` | Images and text files; Pi adds native text wrappers and skips empty files. |

Other agents reject nonempty `attachments`. `prat agents --json` exposes `attachments`,
`attachment_types` and `attachment_max_count` (one for Hermes; null means no Prat count cap).
Native formats, model support and limits still apply. Prat only checks regular-file readability;
it does not decode images or turn attachments into prompt text. Native agents read the canonical
file paths after validation, without a snapshot. See [attachment input](running.md#native-attachments).
