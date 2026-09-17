# Profiles and configuration

A profile saves an agent and its settings under a reusable name. Store profiles globally for use
across projects, or locally in a project's `.pratfile`.

- [Create a profile](#create-a-profile)
- [Define prompt templates](#define-prompt-templates)
- [Choose a config file](#choose-a-config-file)
- [Merge global and local settings](#merge-global-and-local-settings)
- [Configure commands and wrappers](#configure-commands-and-wrappers)
- [Inspect and validate](#inspect-and-validate)
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
`model`, `effort`, `fast`, `timeout`, `native_args`, and the agent's supported budget fields.
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
| `[defaults]` | Merge field by field; local values win. |
| `[profiles.NAME]` | A local profile replaces the entire global profile with that name. |
| `[templates.NAME]` | A local template replaces the entire global template with that name. |
| `[agents.NAME].command` | A local command array replaces the global array for that agent. |
| `native_args` | The higher-priority array replaces the lower-priority array, even when empty. |

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
