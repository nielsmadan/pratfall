# Profiles and configuration

A profile saves an agent and its settings under a reusable name. Store profiles globally for use
across projects, or locally in a project's `.pratfile`.

- [Create a profile](#create-a-profile)
- [Define prompt templates](#define-prompt-templates)
- [Give a profile its own prompt](#give-a-profile-its-own-prompt)
- [Choose a config file](#choose-a-config-file)
- [Merge global and local settings](#merge-global-and-local-settings)
- [Extend a profile](#extend-a-profile)
- [Configure commands and wrappers](#configure-commands-and-wrappers)
- [Inspect and validate](#inspect-and-validate)
- [Extra directories](#extra-directories)
- [Appended instructions](#appended-instructions)
- [Pi profiles](#pi-profiles)
- [Tool availability](#tool-availability)
- [Upgrade existing profiles](#upgrade-existing-profiles)
- [Notifications](#notifications)

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
`model`, `effort`, `fast`, `timeout`, `native_args`, `add_dirs`, `attachments`, `instructions`,
`instructions_file`, `prompt`, `schema`, `tools`, `disabled_tools`, `native_agent`, `session_id`,
`notify`, `notify_method`, `notify_after`, `command_prefix`, and the agent's supported budget
fields.
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

Select it with `prat simple -t review --file change.diff`. `$input`, `${input}` and `$$` have fixed
meanings; case variants of `input` such as `$Input` are rejected, and any other valid `$name` or
`${name}` is a [prompt parameter](running.md#fill-prompt-parameters).
Malformed placeholders fail configuration loading even in unused templates.
See [applying templates](running.md#apply-a-template) for input composition and limits.

A template is a prompt for any agent or profile, chosen per run with `-t`. To keep a prompt together with an agent and its settings, or to give a workflow step a
prompt, [give a profile its own prompt](#give-a-profile-its-own-prompt) instead.

## Give a profile its own prompt

A profile can carry a `prompt` that uses the template syntax. A profile with a `prompt` is a saved
task that runs by name, and it is the way to give a [workflow step](workflows.md) a prompt:

```toml
[profiles.remove-empty]
agent = "claude"
prompt = "Delete empty files under $folder."
```

`prat remove-empty src` fills `$folder`; see
[prompt parameters](running.md#fill-prompt-parameters). `-t NAME` replaces the profile's prompt for
one run. The prompt is validated like a template, even for unused profiles.

- A profile inherits `prompt` through `extends`; the nearest ancestor that sets one wins. A child
  can replace it but cannot clear it.
- A child that `extends` a profile and sets a different `agent` reuses that prompt, so one prompt
  runs on several agents without copying it:

  ```toml
  [profiles.review]
  agent = "claude"
  prompt = "Review this change:\n\n$input"

  [profiles.review-cx]
  extends = "review"
  agent = "codex"
  ```
- `[defaults]` does not accept `prompt`.
- `prat list profiles` shows the prompt and its parameters, and `prat help NAME` the full
  prompt.

## Choose a config file

| Scope | Path |
| --- | --- |
| Global | `$XDG_CONFIG_HOME/pratfall/config.toml`, or `~/.config/pratfall/config.toml` when `XDG_CONFIG_HOME` is absent, empty or relative. |
| Local | The nearest `.pratfile` in the directory where you invoke Prat or one of its parents. |
| Explicit local | `--config PATH`, which replaces automatic `.pratfile` discovery. |

Prat searches the invocation directory, then each parent up to the filesystem root, and uses the
first `.pratfile` it finds; it never merges several local files. It merges that file over the
global config. `--cwd` does not affect config discovery. Relative `--config` paths resolve from
the invocation directory.

Missing global and automatic local files are allowed. A missing file explicitly selected with
`--config` is an error. Every existing file requires `version = 1` and must be a regular file of
at most 4 MiB.

### Trust a `.pratfile`

A `.pratfile` can set agent commands, command prefixes and native arguments, so cloning a
repository and running `prat` in it would otherwise execute whatever that repository chose. Prat
therefore loads an automatically discovered `.pratfile` only when its directory, or a directory
above it, is trusted. Review the file, then trust its directory:

```sh
prat trust            # the invocation directory
prat trust ~/src/team-repo
```

Trust covers the directory and everything under it, so trusting a repository root also covers
running Prat from its subdirectories, and trusting `~/src` covers every repository below it. Edits
to a trusted `.pratfile`, such as a teammate's change arriving with `git pull`, keep it trusted.
Prat records the directory's resolved real path in `$XDG_STATE_HOME/pratfall/trusted.json` (default
`~/.local/state/pratfall/trusted.json`, also used when `XDG_STATE_HOME` is relative).

An untrusted `.pratfile` fails every command, naming the file and the directory to trust:

```text
prat: /src/team-repo/.pratfile: local config is not trusted; review it, then trust its directory and everything under it with: prat trust /src/team-repo
```

Shell completion skips an untrusted `.pratfile` and still offers names from the global config.

List and revoke trusted directories with:

```sh
prat trust --list
prat trust --revoke ~/src/old-clone
```

`--list` prints one trusted directory per line, sorted, and marks a directory that no longer exists
with ` (missing)`; with an empty store it prints nothing. `--revoke [DIR]` removes the entry for DIR
(default: the invocation directory), resolved like `prat trust DIR`. DIR need not exist, so a
`(missing)` entry can be removed by the path `--list` printed. Revoking a directory that was never
trusted reports `DIR was not trusted` and succeeds. A directory that is trusted only through a
trusted parent cannot be revoked on its own: Prat exits 2, names the parent and leaves the store
unchanged. `--list`, `--revoke` and a bare `DIR` cannot be combined.

With `--json`, `prat trust` prints the directory's `path` and `"trusted": true`, `--revoke` prints
`path` and `"trusted": false`, and `--list` prints `directories`, an array of `path` and
`exists` records.

The global config and a file passed with `--config PATH` are trusted because you selected them,
so `prat trust` rejects `--config` with exit 2; trust a directory with `prat trust DIR` instead.

Create a local file with:

```sh
prat --config .pratfile config init
```

`config init` creates a new example and fails if the file already exists. Ordinary runs never
write configuration.

## Merge global and local settings

Settings resolve from highest to lowest priority:

**Invocation flags → selected profile → its `extends` chain → local defaults → global defaults.**

| Setting | Merge rule |
| --- | --- |
| `[defaults]` | Merge field by field; local values win, with the instruction pair grouped below. |
| `[profiles.NAME]` | A local profile replaces the entire global profile with that name. |
| `[templates.NAME]` | A local template replaces the entire global template with that name. |
| `[agents.NAME].command` | A local command array replaces the global array for that agent. |
| `[agents.NAME].command_prefix` | A local prefix replaces the global prefix for that agent, independently of `command`. |
| `schema` | The higher-priority source path replaces the lower-priority path. |
| `native_agent` | The higher-priority string replaces the lower-priority selection. |
| `instructions`, `instructions_file` | One override group: either higher-priority form clears the lower-priority form. |
| `native_args`, `add_dirs`, `attachments`, `tools`, `disabled_tools`, `command_prefix` | The higher-priority array replaces the lower-priority array, even when empty. |

Profiles from every file remain available. A profile inherits only through an explicit
`extends`; omitted fields then fall back to the merged defaults.

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

## Extend a profile

`extends` names a parent profile. The child inherits its fields and overrides what it sets:

```toml
# the global config: bases shared across projects
[profiles.claude-base]
agent   = "claude"
model   = "claude-sonnet-5"
tools   = ["Read", "Edit", "Bash"]
timeout = 1800

[profiles.codex-base]
agent  = "codex"
effort = "medium"
```

```toml
# a project's .pratfile
[profiles.weekly]
extends = "claude-base"
model   = "claude-opus-5"
```

`weekly` runs Claude Code with `claude-opus-5`, the base's tools, and a 1800 second timeout.

This is how backend-specific settings are shared. `[defaults]` cannot do it: defaults apply to
every profile in the file and are validated against each one's agent, so a single `tools` entry
beside any Codex profile fails the whole file. A base profile is validated only against its own
agent and only reaches the children that ask for it.

Rules:

- `extends` takes one profile name. Chains are allowed (`weekly` → `claude-slow` → `claude-base`)
  and give composition without a precedence rule to memorise: the nearest ancestor wins.
- The parent is looked up in the merged namespace of both loaded files, so a `.pratfile` child
  can extend a base defined in the global config.
- A profile with `extends` may omit `agent` and takes its nearest ancestor's. A profile without
  `extends` must declare one. A child may override `agent`; an inherited option the new agent
  does not support is then rejected, naming the parent field that supplied it.
- Arrays replace rather than merge, exactly as they do between files. A child setting
  `tools = ["Skill"]` gets only `Skill`.
- Inherited values beat `[defaults]`, including the defaults of a nearer file: the chain is part
  of the profile, and profiles outrank defaults.

Errors name the file and the field:

```
.pratfile: profiles.weekly.extends: unknown profile 'claude-basee'.
.pratfile: profiles.b.extends: inheritance cycle: a -> b -> a.
```

A profile cannot extend itself, and naming an agent (`extends = "claude"`) reports an unknown
profile because agent names cannot be profile names — inherit the agent from a base instead.

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

Use `command_prefix` to run every agent through the same launcher, such as a secrets injector,
instead of repeating it in each agent's `command`:

```toml
[defaults]
command_prefix = ["sops-exec"]

[agents.droid]
command_prefix = []
```

Prat prepends the prefix to the agent's resolved command, whether built in or set through
`[agents.NAME].command`, so `prat cx` above runs `sops-exec codex`. The prefix resolves in this
order: selected profile and its `extends` chain, then `[agents.NAME].command_prefix`, then local
and global `[defaults]`. An empty array removes an inherited prefix, as for `droid` above. There is
no command-line flag. The prefix follows the same path rules as `command`: a relative first
element containing `/` resolves from the defining config file's directory, and later elements are
literal. `--dry-run` shows the prefixed argv, and `prat doctor --versions` probes through the
prefix while availability still checks the agent's own executable.

If the selected local path is a symlink to the global file, Prat loads it once and reports only the
selected path. Relative commands still resolve from that path's directory.

## Inspect and validate

```sh
prat list
prat list profiles
prat help weekly
prat config validate
prat --config custom.toml list
prat --config custom.toml config validate
```

`prat list` prints profiles, workflows, templates and agents as aligned tables, one row each; `prat
list SECTION` prints one of them, and `--json` returns every field. A profile row shows NAME,
AGENT, EXTENDS, SETS (what the profile itself sets) and FROM: `global` for the global or `--config`
file, `local` for the `.pratfile`, or `local, replaces global` when a `.pratfile` profile replaces
a global one of the same name. SETS shows short values in full and text or list settings, such as
`prompt`, `instructions`, `native_args` and `tools`, by key only, with a prompt's parameters in
brackets: `prompt (topic)`. On a terminal a long SETS cell is cut with `…` to fit the width, never
below 20 columns; piped output is never cut.

`prat help NAME` shows a profile's resolved settings with the origin of each value:

```text
weekly: profile for claude, global (/home/u/.config/pratfall/config.toml)
extends: base
parameters: topic

SETTING  VALUE                 ORIGIN
model    claude-opus-5         profiles.base
effort   high                  profiles.weekly
timeout  600                   built-in default
prompt   "Summarise ${topic}"  profiles.weekly
```

ORIGIN names the profile layer or `defaults` table that set the value, with ` (local)` when it came
from the `.pratfile`; `agents.NAME` for a `command_prefix` from `[agents.NAME]`; or `built-in
default`.

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

`prat trust` is now a management command, so `trust` is a reserved name: rename a
`[profiles.trust]` or `[workflows.trust]`, for example to `[profiles.my-trust]`.

`.pratfile` discovery now searches parent directories. In a subdirectory below an untrusted
`.pratfile`, Prat used to run with the global config only; it now refuses until you review that
file and run `prat trust` on its directory. A `.pratfile` in a parent directory also now applies to
every subdirectory that has none of its own.

In `--dry-run --json` previews, `schema_transport` is now the token `inline` or `file`, replacing
`inline JSON` and `temporary prepared file`. Update scripts that compare it; the text preview keeps
the readable wording.

A native exit status of 2, 124, 126, 127 or 128 and above now makes Prat exit 1, so those codes
keep only their [Prat meaning](json.md#errors-and-partial-results). Other native statuses still
pass through. Scripts that branched on such a native status should read `native_exit_code` from
`--json` output instead.

Two run flags were renamed to match their config keys, with no old spellings kept: `--attach` is
now `--attachment` (key `attachments`) and `--disable-tools` is now `--disabled-tools` (key
`disabled_tools`). Update scripts and aliases that pass the old flags, which now fail as
unrecognized arguments. Native arguments after `--` are not affected.

The new built-ins reserve `goose` and `kilo`. Rename colliding profiles, for example
`[profiles.goose]` to `[profiles.my-goose]`, keeping its agent and settings.

OpenHands and iFlow have been removed: delete or retarget any `[agents.openhands]`,
`[agents.iflow]`, or profiles selecting `openhands`/`oh`/`iflow`/`if`. The `kimi` selector now
requires Kimi Code 2.1.1+, not the retired Python package; remove legacy native flags such as
`--thinking`, `--plan`, and `--final-message-only`. Run `prat config validate` before use.

`templates` is also reserved. Rename an existing `[profiles.templates]` and update invocations
that select it before upgrading. The same applies to `completion`, reserved for the shell
completion command.

`workflows` is also reserved, for the command that lists [workflows](workflows.md). Rename an
existing `[profiles.workflows]` and update invocations that select it. A profile and a workflow
cannot share a name.

One `prat list` command replaces the four listing commands: `prat agents`, `prat profiles`,
`prat templates` and `prat workflows` are now `prat list agents`, `prat list profiles`, `prat list
templates` and `prat list workflows`. With `--json` each prints the same key and records as before,
with new fields added: `extends` and `source` on profiles, `installed` on agents. The text listing
is now an aligned table. `agents`, `profiles`, `templates` and `workflows` are no longer reserved,
so they are legal profile and workflow names again; `list` is now reserved, so rename a
`[profiles.list]` or `[workflows.list]` and update invocations that select it.

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
Claude, Copilot and Pi can represent it; Qwen, Droid, Vibe and unsupported agents reject it.
Claude's MCP tools and, while other tools remain, `EndConversation` are outside its empty built-in
selection. `prat help NAME` displays active `tools=[]` and `prat list agents --json` exposes `tools`,
`disabled_tools`, `tools_empty`, `tools_scope`, and `disabled_tools_scope` capabilities.

Backend-specific defaults affect every profile that inherits them, including unused profiles
validated when the configuration loads. Keep allowlists in profiles when mixing agents: an empty
allowlist does not neutralize an unsupported inherited `tools` field. Native flag collisions and
unrepresentable list delimiters fail before prompt input; ordinary config inspection reads no
auxiliary resources and launches no native command.

## Configure native agent selection

```toml
[profiles.review]
agent = "claude"
native_agent = "reviewer"
```

`agent` chooses the Prat backend, `review` names the Prat profile, and `native_agent` selects an
existing native persona. Claude, Copilot and Vibe support this string. CLI `--native-agent`
overrides the profile, local defaults and global defaults, in that order. Omission inherits;
empty or whitespace-only names do not clear a selection. Names must be NUL-free valid UTF-8
and are forwarded literally. `prat help NAME` includes the resolved selection.

Backend-specific defaults apply to every profile: putting `native_agent` in `[defaults]` makes
an unsupported profile invalid even if it is not selected. Config validation and listing do not
discover native names or read/write agent definitions. The selected persona can change native
permissions, including automatic approval with Vibe's `auto-approve` agent. See
[native agent selection](running.md#select-a-native-agent) for native collisions and limitations.

## Attachments

```toml
[profiles.visual]
agent = "codex"
attachments = ["images/screenshot.png", "images/detail.png"]
```

`attachments` is an ordered array of nonempty path strings. Paths resolve relative to the defining
configuration file, independently for global defaults, local defaults and profiles. CLI
`--attachment` paths use the invocation directory, even with `--cwd`. A higher-priority array replaces the whole
inherited list; `attachments = []` clears it and is neutral on every backend. Defaults affect every
profile, so unsupported profiles must clear backend-specific attachments or config validation fails.
Hermes accepts at most one attachment, including when validating an unused profile.

Loading, listing and validating configuration do not open attachment files. Only the selected
invocation validates its winning files before prompt acquisition; missing files in unused profiles
or overridden arrays are not read. Selected paths become canonical filesystem targets to preserve
symlink/`..` traversal. Each must be regular and readable. Prat checks a single byte without text
conversion and passes the path to the native agent. Files can change after validation; there is no
snapshot. Native image/document formats, model support and size limits remain native-authoritative.
See [running with attachments](running.md#native-attachments) for restrictions and task input.

## Configure schema output

```toml
[profiles.structured]
agent = "claude"
schema = "schemas/answer.json"
```

Schemas are supported for Claude, Codex and Qwen (v0.24.0+).
`schema` is a nonempty path string relative to its defining config file. CLI `--schema PATH`
overrides it relative to the invocation directory. Prat preserves symlink/`..` traversal and reads
only the winning selected file, before prompt acquisition. `prat help NAME` shows source paths;
`prat config validate` checks structure and capabilities without opening schema files.
Schema defaults inherited by an unsupported profile invalidate the configuration even when that
profile is not selected. Use agent-specific profiles for backend-specific defaults.
See [schema output](running.md#request-schema-output) for input limits and native validation.


## Pi profiles

A Pi profile can select its model, thinking level, tool filters and attachments:

```toml
[profiles.pi_review]
agent = "pi"
model = "anthropic/claude-sonnet-4-5"
effort = "high"
tools = ["read", "grep", "find", "ls"]
disabled_tools = ["bash"]
attachments = ["notes/context.txt"]
```

Use a model available in your native Pi configuration. All normal option precedence and
config-relative attachment path rules apply. See [Pi behavior](agents.md#pi).

## Notifications

`notify`, `notify_method`, and `notify_after` follow the same precedence as other options:
CLI → profile → local defaults → global defaults. Only `notify` has CLI flags
(`--notify` / `--no-notify`); the other two are set in config only. See
[get a completion notification](running.md#get-a-completion-notification) for the gate
conditions, the `notify_method` values, and what each terminal emulator receives.
