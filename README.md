# Pratfall

<img src="assets/logo.svg" alt="Pratfall logo" width="128" height="128">

One command to run your coding agents. Pratfall wraps installed agent CLIs with
[short aliases](#aliases), [common parameters](#common-parameters), and reusable
[local and global profiles](#profiles).

```sh
prat cx "review this change"
prat cc --effort high "investigate this failure"
```

## Installation

Requires Python 3.13+ and an installed, authenticated native agent CLI. Until the first release,
run from a source checkout with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run prat --help
uv run prat doctor
```

Prefix the examples below with `uv run`, or use `just install` to install the checkout as a
user-level `prat` command. See [getting started](docs/user/getting-started.md) for setup details.

## Aliases

Choose an agent by name or short alias:

| Agent | Name | Alias |
| --- | --- | --- |
| Claude Code | `claude` | `cc` |
| Codex | `codex` | `cx` |
| Gemini | `gemini` | `gm` |
| Copilot | `copilot` | `cp` |
| Cursor | `cursor` | `cu` |
| OpenCode | `opencode` | `oc` |
| Pi | `pi` | none |

Use `prat pi "review this change"` with Pi v0.85.1 or newer; model, effort, tool filters and
file/image attachments are supported. Pi trims prompt-edge whitespace.

These are a few of the supported agents. Run `prat agents` for the full list and supported
settings, or see [all selectors and agent limitations](docs/user/agents.md).
Names of four letters or fewer, such as `kiro`, `qwen`, and `amp`, use the name itself.

## Common parameters

Use the same flags across agents, before or after the selector:

```sh
prat cx --model gpt-5.6-luna --effort low "review this change"
prat cc --file request.md --timeout 120 --progress
printf 'summarize this checkout\n' | prat gm
```

| Parameter | Purpose |
| --- | --- |
| `--model MODEL` | Select a native model. |
| `--native-agent NAME` | Select a native agent/persona for Claude, Copilot, or Vibe. |
| `--session-id ID` | Set the native session id so its transcript can be found afterwards. |
| `--effort EFFORT` | Set reasoning effort. |
| `--fast` / `--no-fast` | Override fast mode for Claude or Codex. |
| `--timeout SECONDS` | Set the execution deadline; default: 600 seconds. |
| `--cwd PATH` | Choose the agent's working directory. |
| `--attach PATH` | Attach a native image/file; repeat where supported. |
| `--add-dir PATH` | Add a native workspace directory; repeat for several. |
| `--instructions TEXT` / `--instructions-file PATH` | Append native instructions from text or a UTF-8 file. |
| `--tools NAME` / `--disable-tools NAME` | Restrict native tool availability or disable names/patterns; repeat for several. |
| `-f PATH` / `--file PATH` | Read a prompt from a UTF-8 file; `-` reads stdin. |
| `-t NAME` / `--template NAME` | Apply a named prompt template. |
| `-e` / `--edit` | Edit the complete prompt in `VISUAL`, `EDITOR`, or `vi`. |
| `--context PATH` | Prepend a context file; repeat to include several. |
| `-x` / `--extract` | Return the body of the first fenced code block. |
| `--schema PATH` | Request native JSON Schema output from a UTF-8 file (Claude, Codex, Qwen 0.24+). |
| `--json` | Return one JSON result with output, status, and available usage. |
| `--progress` | Show live activity on stderr. |
| `--trace` | Copy captured native stdout to stderr. |
| `--dry-run` | Preview the resolved invocation without launching the agent. |

Model, effort, fast mode, extra directories, instructions, tool availability, native agent selection, schemas, and budget support depend on the agent; unsupported settings are
rejected. Native budgets are available through `--max-budget-usd`, `--max-turns`, and
`--max-ai-credits` where supported.

Pass one prompt as text, a file, or redirected stdin. Use `--prompt=TEXT` for text starting with
a dash. When stdin is redirected alongside text or `--file PATH`, Prat combines stdin first,
then two newlines, then the explicit prompt:

```sh
prat cx "how many Rs in strawberry" | prat cx "times 5"
```

Supported native arguments go after `--`, for example:

```sh
prat cx "inspect this change" -- --sandbox read-only
```

Use `prat cx --attach screenshot.png "explain this screenshot"` for native image input.
Codex and Hermes support images (Hermes accepts one), Copilot supports images/native documents,
and OpenCode supports files. Native model and format requirements still apply.

Use `prat cx --add-dir ../shared "review both projects"` to include an extra directory.
Claude, Codex, Gemini, Qwen and Copilot support this setting with their native access semantics.
Paths resolve from the invocation directory, independently of `--cwd`.

Use `prat cc --tools Read --disable-tools 'mcp__*' "review this file"` to select native tools.
Claude, Qwen, Copilot, Droid and Vibe support tool controls with different
[scopes and native names](docs/user/running.md#control-tool-availability). These flags do not
add permission approvals or provide an OS sandbox. Claude's allowlist affects built-ins only;
Qwen's schema tool bypasses its allowlist but obeys native denials.

Use `prat cc --instructions "Cite file paths" "review this change"` or
`prat cx --instructions-file rules.md "review this change"` for additional native guidance.
Claude, Codex, Qwen and Droid support instructions. Codex replaces any native configured
`developer_instructions` value for this invocation. See [instruction rules and limits](docs/user/running.md#append-instructions).

Use `prat cc --session-id "$(uuidgen)" "review this change"` to set the native session id up front. Claude, Copilot and Grok support this; a fresh UUID always creates a new session, while a value matching an existing Copilot session resumes it. `--json` reports the session as `native_session_id` for those plus Codex and Cursor, falling back to the requested value when a run ends before the agent reports one. See
[session ids](docs/user/running.md#name-the-session).

Use `prat cc --native-agent reviewer "review this change"` to select an existing native agent.
This differs from a Prat profile or backend selector. The selected persona may change native
permissions; Vibe's `auto-approve` agent permits automatic tool approval. Prat adds no approval
flags. See [native agent selection](docs/user/running.md#select-a-native-agent).

Prat preserves native authentication and the selected native agent's permission behavior. See
[running prompts](docs/user/running.md) for input rules and run behavior, and
[JSON output](docs/user/json.md) for result fields and errors.

Use `prat cc --schema answer.schema.json --json "summarize this project"` for structured output.
`output` remains a string containing compact JSON; `structured_output` also contains the parsed
answer. Schema files have a 1 MiB limit and cannot be combined with `--extract`.
See [schema output](docs/user/running.md#request-schema-output) for validation and native limits.

Use `prat cc -x "Write a Python function"` to extract the first fenced block from an answer.
Missing or unclosed fences leave the answer unchanged; an empty block produces empty output.
Extraction also applies to JSON `output` and partial failed answers. See
[code extraction](docs/user/running.md#extract-code) for fence and newline rules.

## Profiles

Save an agent and its settings under a name, then use that name like an alias.
Both global and local configuration use the same TOML format:

```toml
version = 1

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
```

```sh
prat simple "review this change"
prat simple --effort high "investigate this failure"
```

| Scope | Config file | Create an example |
| --- | --- | --- |
| Global | `~/.config/pratfall/config.toml`¹ | `prat config init` |
| Local | `.pratfile` in the invocation directory | `prat --config .pratfile config init` |

¹ Uses `$XDG_CONFIG_HOME/pratfall/config.toml` when `XDG_CONFIG_HOME` is set and nonempty.

Global profiles are available across projects. Local configuration merges over global
configuration; a local profile with the same name replaces the entire global profile.
Prat reads `.pratfile` only in the invocation directory, without searching parent directories.
`--cwd` does not change config discovery; `--config PATH` selects a different local file.

Settings resolve in this order: **command-line flags → profile → its `extends` chain → local
defaults → global defaults**. Use `extends` to share backend-specific settings through a base
profile, `[defaults]` for settings every agent accepts, `prat profiles` to inspect resolved
profiles, and `prat config validate` to check configuration.

```toml
[profiles.claude-base]
agent = "claude"
tools = ["Read", "Edit", "Bash"]

[profiles.weekly]
extends = "claude-base"
model   = "claude-opus-5"
```

See [profiles and configuration](docs/user/profiles.md) for defaults, command wrappers, and
merging rules.

Save reusable prompts separately from agent profiles:

```toml
[templates.review]
prompt = "Review the following for bugs:\n\n$input"
```

```sh
git diff | prat simple -t review
prat templates
```

Templates support `$input`, `${input}`, and `$$` for a literal dollar sign. A template without an
input placeholder appends supplied input after two newlines and can run on its own. See
[prompt templates](docs/user/running.md#apply-a-template) for composition and validation rules.

Add local files with `prat cc "review this" --context notes.md --context design.md`.
[Context files](docs/user/running.md#include-context-files) precede the templated task in the order
supplied, with quoted path labels. The complete prompt must fit within 1 MiB.

Use `prat cx --edit` to write a prompt in your editor, or add `--edit` to revise the complete
stdin, template and context draft before execution. Editing needs a controlling terminal and
cannot be combined with `--dry-run`. See [editor prompts](docs/user/running.md#edit-the-prompt).

## More

- [User guides](docs/user/overview.md)
- [Contributing](CONTRIBUTING.md) and [developer documentation](docs/overview.md)
- [MIT license](LICENSE)
