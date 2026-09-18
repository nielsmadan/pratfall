# Profiles and configuration

A profile saves an agent and its settings under a reusable name. Store profiles globally for use
across projects, or locally in a project's `.pratfile`.

- [Create a profile](#create-a-profile)
- [Define prompt templates](#define-prompt-templates)
- [Choose a config file](#choose-a-config-file)
- [Merge global and local settings](#merge-global-and-local-settings)
- [Configure commands and wrappers](#configure-commands-and-wrappers)
- [Inspect and validate](#inspect-and-validate)
- [Extra directories](#extra-directories)
- [Appended instructions](#appended-instructions)
- [Tool availability](#tool-availability)
- [Upgrade existing profiles](#upgrade-existing-profiles)

## Create a profile

Create an example config, then edit it:

```sh
prat config init
prat config path
```

Both global and local files use the same TOML format:

```toml
version = 1

[defaults]
timeout = 600

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
```

Use the profile name as the selector. Flags override its settings for one run:

```sh
prat simple "review this change"
prat simple --effort high "investigate this failure"
```

Each profile requires an `agent`, given as a [full name or alias](agents.md). Optional fields are
`model`, `effort`, `fast`, `timeout`, `native_args`, `add_dirs`, `instructions`,
`instructions_file`, `tools`, `disabled_tools`, and the agent's supported budget fields.
Use `[defaults]` for shared settings.

`fast` accepts `true` or `false` for Claude and Codex. Both values override the native setting;
omitting the field preserves it. Other agents reject either value.

## Define prompt templates

`[templates.NAME]` defines a reusable prompt independently of any agent or profile. Its only field
is the required, nonempty `prompt` string. Names start with a letter or digit, followed by letters,
digits, underscores or hyphens. Template names have their own namespace: a template can share a
name with a profile, agent or management command.

```toml
[templates.review]
prompt = "Review this change:\n\n$input"
```

Select it with `prat simple -t review --file change.diff`. Only `$input`, `${input}`, and `$$` are
supported; malformed or unknown placeholders fail configuration loading even in unused templates.
See [applying templates](running.md#apply-a-template) for input composition and limits.

## Choose a config file

| Scope | Path |
| --- | --- |
| Global | `$XDG_CONFIG_HOME/pratfall/config.toml`, or `~/.config/pratfall/config.toml` when `XDG_CONFIG_HOME` is absent or empty. |
| Local | `.pratfile` in the directory where you invoke Prat. |
| Explicit local | `--config PATH`, which replaces automatic `.pratfile` discovery. |

Prat merges the selected local file over the global config. It does not search parent directories,
and `--cwd` does not affect config discovery. Relative `--config` paths resolve from the invocation
directory.

Missing global and automatic local files are allowed. A missing file explicitly selected with
`--config` is an error. Every existing file requires `version = 1`.

Create a local file with:

```sh
prat --config .pratfile config init
```

`config init` creates a new example and fails if the file already exists. Ordinary runs never
write configuration.

## Merge global and local settings

Settings resolve from highest to lowest priority:

**Invocation flags → selected profile → local defaults → global defaults.**

| Setting | Merge rule |
| --- | --- |
| `[defaults]` | Merge field by field; local values win, with the instruction pair grouped below. |
| `[profiles.NAME]` | A local profile replaces the entire global profile with that name. |
| `[templates.NAME]` | A local template replaces the entire global template with that name. |
| `[agents.NAME].command` | A local command array replaces the global array for that agent. |
| `instructions`, `instructions_file` | One override group: either higher-priority form clears the lower-priority form. |
| `native_args`, `add_dirs`, `tools`, `disabled_tools` | The higher-priority array replaces the lower-priority array, even when empty. |

Profiles from both files remain available. Profiles do not inherit from each other; omitted fields
use the merged defaults.

For example, this `.pratfile` replaces the global `simple` profile above. It changes the model and
drops `effort = "low"`:

```toml
version = 1

[profiles.simple]
agent = "codex"
model = "project-model"
```

Replace `project-model` with a model supported by your agent. Other global profiles remain
available.

Prat warns on stderr when a local profile replaces a global one, naming the profile and both files.
This also applies to `--json`. With `--progress`, a slow stderr reader can cause warnings to be
dropped.

Templates follow the same replacement and warning rules, naming the template and both source
files. Definitions in both files are validated before replacement, so an invalid global template
cannot be hidden by a local override.

## Configure commands and wrappers

Use `[agents.NAME].command` to set an executable and any fixed arguments:

```toml
[agents.codex]
command = ["codex"]
```

Bare executable names use `PATH`. Relative executable paths containing `/` resolve from the config
file's directory. Arguments are literal strings: Prat performs no shell, variable, or tilde
expansion.

To use a shell function, expose it through a trusted executable wrapper and configure that command.
See [configuration examples](../../examples/README.md).

If the selected local path is a symlink to the global file, Prat loads it once and reports only the
selected path. Relative commands still resolve from that path's directory.

## Inspect and validate

```sh
prat profiles
prat templates
prat config validate
prat --config custom.toml profiles
prat --config custom.toml config validate
```

`config path` and `config init` target the global file unless `--config PATH` is supplied.
`config validate` checks the merged configuration and lists the loaded files.

All files are checked for syntax, schema, and field types before merging. Every effective profile
is then validated with its defaults. Defaults are checked against a built-in agent's capabilities
when that selector is resolved. Errors name the source file and field, including inherited settings.

In JSON results, `sources` lists loaded files in global-to-local order. `path` is the
highest-priority loaded file, or the global path if neither file exists.

## Extra directories

Use `add_dirs` in a profile or `[defaults]`:

```toml
[profiles.monorepo]
agent = "codex"
add_dirs = ["../shared", "../other project"]
```

Claude, Codex, Gemini, Qwen and Copilot support extra directories. Each relative path uses the
file defining that value as its base, including inherited global defaults. `~` expands to the
user's home. Paths preserve directory-symlink traversal through `..`; no shell or variable
expansion occurs. A higher-priority list replaces the entire inherited list. `add_dirs = []`
clears inherited directories and is neutral even for an agent without directory support.

Defaults apply to every profile. An unsupported profile must clear `add_dirs` explicitly or
whole-config capability validation fails, even when another profile is selected. The inventory
shows resolved paths. Configuration listing and validation do not check directory existence;
only the selected run checks directories, before reading the prompt or opening an editor.
Gemini and Qwen reject paths containing commas or ending in whitespace because their native
parsers split commas and trim values. A nonempty public list conflicts with the corresponding
native directory flags in `native_args`; empty lists leave native flags usable.

Selected directory paths are canonicalized through the filesystem before native argv is built.
This preserves symlink/`..` targets even when a native parser would otherwise collapse `..`
lexically. Config inspection retains the joined source spelling and does not inspect targets.

The Gemini/Qwen delimiter restrictions apply to both configured spellings and selected canonical
targets. Configuration validation rejects an unrepresentable spelling without inspecting its target.

## Appended instructions

```toml
[profiles.review]
agent = "claude"
instructions_file = "rules/review.md"
```

Use either `instructions = "Cite file paths"` or `instructions_file = "rules.md"` in any profile
or `[defaults]`, for Claude, Codex, Qwen or Droid. Both fields in the same table are an error.
They are one override group across CLI, profile, local defaults and global defaults: a higher
layer's text replaces an inherited file, and a higher layer's file replaces inherited text.
Only the winning field retains source provenance. Blank text is invalid, not a clearing value.

A file path is based on its defining config's directory even when inherited. Configuration
listing and validation check types, inline text, capabilities and native conflicts without
opening instruction files. Only a selected run reads its winning file, before prompt acquisition.
Backend-specific defaults affect every profile: an unsupported profile makes whole-config
capability validation fail even when another profile is selected. Put instructions on supported
profiles when sharing a config across other backends.

Codex replaces its native configured `developer_instructions` value for the invocation, while
retaining its built-in guidance. See [content bounds and native mappings](running.md#append-instructions).

## Upgrade existing profiles

Built-in names, aliases, and management commands are reserved profile names. When an upgrade
introduces a collision, rename your profile, update commands that use it, and run
`prat config validate`.

This release reserves `openhands`, `oh`, `warp`, `iflow`, `if`, `qwen`, `amp`, `reasonix`, `rx`,
`droid`, `dr`, `kimi`, `vibe`, `crush`, `cr`, `devin`, `dv`, `cortex`, `co`, and `grok`.
For example, rename `[profiles.oh]` to `[profiles.my-openhands]`, keeping its agent and settings.

`templates` is also reserved. Rename an existing `[profiles.templates]` and update invocations
that select it before upgrading.

A collision reports `profiles.NAME: name is reserved; choose a different profile name.`

Agents with names of four letters or fewer now use their full names. Update commands and profile
`agent` fields that use these former aliases:

| Former alias | Use instead |
| --- | --- |
| `ki` | `kiro` |
| `wp` | `warp` |
| `qw` | `qwen` |
| `km` | `kimi` |
| `mv` | `vibe` |

The former aliases are available as custom profile names.

## Tool availability

```toml
[profiles.read]
agent = "claude"
tools = ["Read", "Grep"]
disabled_tools = ["mcp__*"]

[profiles.no_tools]
agent = "copilot"
tools = []
disabled_tools = []
```

`tools` and `disabled_tools` are arrays of nonempty native names or patterns. Each array replaces
its inherited value: CLI → profile → local defaults → global defaults. Omitting a field preserves
native behavior. `disabled_tools = []` clears Prat's inherited exclusions and is neutral even on
unsupported agents; it does not clear native permission denials.

`tools = []` actively requests no tools in the [documented native scope](running.md#control-tool-availability).
Claude and Copilot can represent it; Qwen, Droid, Vibe and unsupported agents reject it.
Claude's MCP tools and, while other tools remain, `EndConversation` are outside its empty built-in
selection. `prat profiles` displays active `tools=[]` and `prat agents --json` exposes `tools`,
`disabled_tools`, `tools_empty`, `tools_scope`, and `disabled_tools_scope` capabilities.

Backend-specific defaults affect every profile that inherits them, including unused profiles
validated when the configuration loads. Keep allowlists in profiles when mixing agents: an empty
allowlist does not neutralize an unsupported inherited `tools` field. Native flag collisions and
unrepresentable list delimiters fail before prompt input; ordinary config inspection reads no
auxiliary resources and launches no native command.
