# Native agent verification

## Local audit, 2026-10-13

This audit separates documentation/source checks, executable discovery, version probes, and
authenticated one-shot runs. Discovery and fake-native tests do not establish live compatibility.
Individual [agent references](overview.md) hold the source evidence and contract limitations.

`uv run prat doctor --json` discovered eight configured commands. Its `sops-exec` entries resolve
configured wrappers; the version results below confirm the native commands where probes succeeded.

| Agent | Local version probe | Example status |
| --- | --- | --- |
| Claude | 2.1.296 | Not rerun in this audit |
| Codex | 0.162.1 | Not rerun in this audit |
| Gemini | Doctor's three-second probe timed out | Blocked before inference reading `~/.gemini/trustedFolders.json` |
| Antigravity | 1.1.11 | Not run: below the adapter's required 1.1.15 |
| OpenCode | Doctor's three-second probe timed out | Not rerun in this audit |
| Qwen | 0.23.0 | Blocked before inference reading `~/.qwen/settings.json`; also below the documented 0.23.3 baseline and 0.24 schema requirement |
| Droid | 0.228.0 | `exec --help` blocked reading `~/.factory/settings.json`; no inference attempted |
| Pi | 1.0.1 | Not rerun in this audit |

Before the catalog refresh, the remaining sixteen commands were absent from discovery: Copilot, Kiro, Cursor, OpenClaw,
Hermes, OpenHands, Warp/Oz, iFlow, Amp, Reasonix, Kimi, Vibe, Crush, Devin, Cortex, and Grok.

Gemini and Qwen both returned native exit 52 and empty output. `nono why --self` confirmed
`path_not_granted` for each named file and for Droid's settings file. These failures establish a
local access blocker, not authentication failure or adapter incompatibility. The global install
destinations `/opt/homebrew/bin` and `~/.local/bin` have read-only grants in this session.
The user will perform all installations, authentication and real examples in their own terminal.
These historical agent-side failures do not block that workflow.

## OpenCode 2.0.32 runs, 2026-10-18 to 2026-10-21

The user ran these in their own terminal with `command_prefix = ["sops-exec"]`, where `opencode`
resolved to 2.0.32.

| Command | Result |
| --- | --- |
| `prat oc 'Reply with exactly PRAT_OK.' --model openrouter/xiaomi/mimo-v2.6-pro --effort high --dry-run` | Probe selected 2.x: `sops-exec opencode run --standalone --format json --model 'openrouter/xiaomi/mimo-v2.6-pro#high'` |
| The same with `--json` instead of `--dry-run` | `native_exit`: `Variant unavailable for openrouter/xiaomi/mimo-v2.6-pro: high`; the model offers no `high` variant |
| The same without `--effort`, before the dropped-finish handling | `protocol_error` with output `PRAT_OK`, native exit 0, no `step_finish` ([anomalyco/opencode#53207](https://github.com/anomalyco/opencode/issues/53207)) |
| The same after the dropped-finish handling | `status: "success"`, output `PRAT_OK`, `usage` and `cost_usd` null |

1.x and an effort-capable 2.x model were not rerun. `prat doctor --versions` keeps its three-second
probe, which timed out for OpenCode in the audit above; runs use a 15-second probe.

## Repeatable example

Use a disposable empty directory and a bounded prompt after completing the native agent's setup:

```sh
mkdir -p "$HOME/wrksp/pratfall-agent-smoke"
uv run prat --timeout 60 --json \
  --cwd "$HOME/wrksp/pratfall-agent-smoke" AGENT \
  'Reply with exactly PRAT_OK. Do not use tools or access files.'
```

Replace `AGENT` with the supported selector. This runs real inference and can incur the native
provider's charges. Success requires `status: "success"`, exit zero, and the expected answer.
Record the version, command, result, and any setup blocker; a help command or failed authentication
attempt does not count as a successful example. The prompt is an instruction, not a tool sandbox;
native permissions remain in effect.

## Documentation audit and installation queue

Three read-only research workers checked the nineteen agents other than Claude, Codex, OpenCode,
Droid, and Pi; Droid was additionally checked in the main session. This was a bounded primary-source
audit, not a complete revalidation of every optional flag or every current package decoder.
No new native agent was installed by the agent. No successful authenticated example was recorded.
Implementation now removes OpenHands/iFlow, replaces legacy Kimi with Kimi Code, adds Goose/Kilo,
and refreshes Reasonix, Copilot subagent filtering, Crush effort and Droid session reporting.

Run installation and interactive authentication commands in your own terminal. Credentials should
stay in native setup or the existing secret-injection mechanism, never in this document. Complete
one agent's setup and the example above before proceeding to the next.

| Agent | Verified installation or update | Setup and audit result |
| --- | --- | --- |
| Goose | `brew install block-goose-cli` | Run `goose configure`; choose your provider and authenticate. [New adapter](goose.md) supports stdin, model, native turns, final history text and USD cost. |
| Kilo | `npm install -g @kilocode/cli` | Run `kilo auth login`. [New adapter](kilo.md) uses the OpenCode-compatible stream with daemon attachment disabled. |
| Gemini | `brew install gemini-cli` for a new installation; `brew upgrade gemini-cli` for the existing Homebrew installation | [Install](https://geminicli.com/docs/get-started/installation) and [auth](https://geminicli.com/docs/get-started/authentication): cached Google login or `GEMINI_API_KEY`. Main JSON/token contract matches current source. Local run blocked as above. |
| Antigravity | `agy update` | [Install/auth](https://antigravity.google/docs/cli/install/): authenticate interactively with `agy`; setting `GEMINI_API_KEY` alone has no effect. Current stream documentation matches the adapter. Upgrade 1.1.11 before testing. |
| Copilot | `brew install copilot-cli` | [Official README](https://github.com/github/copilot-cli/blob/main/README.md): `copilot login`, active subscription. Documented flags and 1.0.97 package schema checked; child-message/error filtering corrected. Test native no-tools behavior without adding blanket tool approval. |
| Kiro | `curl -fsSL https://cli.kiro.dev/install \| bash` | [Headless setup](https://kiro.dev/docs/cli/headless/): `KIRO_API_KEY` and an eligible paid subscription; organization policy may restrict keys. V3 documentation corrected in [reference](kiro.md). Complete JSONL event schema remains unverified. |
| Cursor | `curl https://cursor.com/install -fsS \| bash` | [Install](https://cursor.com/docs/cli/installation), [auth](https://cursor.com/docs/cli/reference/authentication): `agent login` or `CURSOR_API_KEY`; `agent status`. JSON envelope matches; latest package delimiter parsing not repeated. |
| OpenClaw | `curl -fsSL https://openclaw.ai/install.sh \| bash -s -- --no-onboard` | [Install](https://docs.openclaw.ai/install): Node 24.16+ or 26.1+, recommending 26. Configure a provider before the example. [Embedded agent exec](https://docs.openclaw.ai/cli/agent) needs no Gateway. Current result projection matches; installer skips onboarding. |
| Hermes | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` | [Official README](https://github.com/NousResearch/hermes-agent/blob/main/README.md): `hermes model` configures provider authentication. Quiet text invocation remains usable; new structured alternative and fixed one-shot failure exits are recorded in [reference](hermes.md). |
| Warp/Oz | `brew tap warpdotdev/warp`, then `brew install --cask oz` | [CLI reference](https://docs.warp.dev/reference/cli/): `oz login` or `WARP_API_KEY`. Legacy interface remains supported; obsolete September deadline removed. Current NDJSON event set matches. |
| Qwen | `npm install -g @qwen-code/qwen-code@0.25.1` | [Official README](https://github.com/QwenLM/qwen-code/blob/main/README.md): Node 22+, interactive `/auth`. Current options/result builder agree with the mapped controls. Installed 0.23.0 is older; no latest-version live compatibility claim. Use the original installation's package manager when upgrading to avoid competing PATH entries. |
| Amp | `curl -fsSL https://ampcode.com/install.sh \| bash` | [CLI docs](https://ampcode.com/docs/cli): `amp login` or `AMP_API_KEY`; version is `amp version`. Current streaming result schema matches. Native tool permissions apply. |
| Reasonix | `npm install -g reasonix@2.37.0` | `reasonix setup`. [Current release audit](reasonix.md) verifies the moved source and unchanged core protocol. Native workspace trust and sandbox availability determine permissions. |
| Kimi Code | `npm install -g @moonshot-ai/kimi-code@2.1.1` | Node 22.19+, then `kimi login`. The [replacement adapter](kimi.md) targets the maintained successor; remove legacy `kimi-cli` using its original package manager if it shadows the new executable. |
| Vibe | `uv tool install mistral-vibe==2.26.1` | [Release README](https://github.com/mistralai/mistral-vibe/blob/v2.26.1/README.md): `MISTRAL_API_KEY` or `vibe --setup`. Main invocation remains; changed finalization and agent inventory recorded in [reference](vibe.md). |
| Crush | `brew install charmbracelet/tap/crush` | [Official README](https://github.com/charmbracelet/crush/blob/main/README.md): configure a provider/key and model. 0.98.1 retains quiet stdin invocation; native effort is now mapped by Prat. |
| Devin | `brew install --cask devin-cli` | [Quickstart](https://docs.devin.ai/cli): `devin auth login`, `devin auth status`, and interactive workspace trust before headless use. Current documented invocation matches; untrusted directories fail in print mode. |
| Cortex | `curl -qLsS https://ai.snowflake.com/static/cc-scripts/install.sh \| sh` | [Setup](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli): macOS Apple Silicon/Intel, paid Snowflake account or dedicated CoCo trial, connection/auth and `SNOWFLAKE.CORTEX_USER` or `SNOWFLAKE.CORTEX_AGENT_USER`. Ordinary Snowflake trials do not qualify. Native turn-limit description corrected. |
| Grok | `curl -fsSL https://x.ai/cli/install.sh \| bash` | [Official README](https://github.com/xai-org/grok-build), [headless guide](https://docs.x.ai/build/cli/headless-scripting): `grok login` or `XAI_API_KEY`. Current headless result shape remains consistent; full current accounting implementation and latest binary version not established. |

Installation commands above come from official guidance; exact pins select the referenced package
version. Package releases and subscription eligibility can change. Authenticated behavior remains
unverified until the user completes the native examples.

## Removed and deferred integrations

- **OpenHands CLI:** [upstream](https://github.com/OpenHands/OpenHands-CLI/blob/main/README.md)
  says: “This project is no longer actively maintained.” Its replacement Agent Canvas starts a
  persistent local stack rather than offering the reviewed one-shot CLI. Removed from the catalog.
- **iFlow:** [upstream](https://github.com/iflow-ai/iflow-cli/blob/main/README.md) announced shutdown
  on April 17, 2026. The old BYOK package remaining downloadable does not establish maintenance.
  Removed from the catalog.
- **Legacy Kimi:** [upstream](https://github.com/MoonshotAI/kimi-cli) is archived. Replaced by the
  maintained Kimi Code contract under the same selector.
- **Roo Code:** [upstream](https://github.com/RooCodeInc/Roo-Code/blob/main/README.md) says:
  “The Roo Code Extension was shut down on May 15th.” Not added.
- **Cline:** maintained, but [normal CLI startup](https://github.com/cline/cline/blob/main/apps/cli/src/main.ts)
  calls `saveProviderSettings`, persisting selected provider/model and resolved API-key data.
  Deferred because adding it would introduce these native configuration side effects into the
  one-shot workflow. A separate explicit integration decision is needed.
- **Aider:** not archived, but the last observed release is v0.86.0 from August 2025 and repository
  activity observed in this audit ended May 2026. Maintenance is uncertain, not proven abandoned.
  Deferred pending stronger current maintenance evidence from [upstream](https://github.com/Aider-AI/aider).
- **Junie:** [headless docs](https://junie.jetbrains.com/docs/junie-headless.html) establish prompt
  modes, but the inspected official documentation did not establish a complete final-result and
  failure-exit contract. Deferred pending that evidence; not classified as unmaintained.
