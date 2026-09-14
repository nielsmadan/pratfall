# Profiles and configuration

The global config is `$XDG_CONFIG_HOME/pratfall/config.toml`, falling back to
`~/.config/pratfall/config.toml`. An empty `XDG_CONFIG_HOME` uses the fallback. Prat automatically
reads a TOML `.pratfile` in the directory where it was invoked and merges it over the global
config. Parent directories are not searched, and `--cwd` does not affect discovery.

`--config PATH` selects a local file instead of `.pratfile`, with relative paths resolved from
the invocation directory. It is merged over the global config too. Missing global and automatic
local files are allowed; a missing explicit file fails. Every existing file requires `version = 1`
and uses the same schema:

```toml
version = 1

[defaults]
timeout = 600
# fast = true

[agents.codex]
command = ["codex"]

[profiles.simple]
agent = "codex"
model = "gpt-5.6-luna"
effort = "low"
```

A profile requires `agent`, using a [full agent name or alias](agents.md). It may set `model`,
`effort`, `fast`, `timeout`, `native_args`, and agent-backed budget fields. Invocation flags override
the selected profile, which overrides local defaults, then global defaults. Defaults merge field
by field. Native argument arrays replace the lower-precedence array, including an empty array.

Profiles from both files are available. A local profile with the same name replaces the entire
global profile; its omitted fields inherit the merged defaults. Profiles do not inherit from
each other. Prat prints one warning per duplicate profile to stderr, naming the profile and both
source files. Warnings also appear with `--json`, keeping stdout available for one JSON result.
With `--progress`, warnings use the nonblocking stderr writer and may be dropped under
backpressure. Validation errors name the file and field that supplied the rejected setting,
including inherited defaults.

For example, if the global config defines `simple` above, this `.pratfile` changes its model and
drops the profile's `effort = "low"` setting:

```toml
version = 1

[profiles.simple]
agent = "codex"
model = "project-model"
```

Run it with `prat simple "review this change"`. Any other global profiles remain available.

`fast` is an optional boolean for Claude and Codex. `true` and `false` are both explicit overrides;
when absent, the native setting remains in force. A global default is validated against a built-in
agent only when that selector is resolved, while every effective profile is validated after merging
both files and their defaults. All files are checked for syntax, schema, and field types before
merging. Unsupported agents reject either boolean value.

`[agents.NAME].command` accepts an executable argv prefix. Bare executable names use `PATH`.
Local command arrays replace global command arrays for the same agent. Relative executable paths
containing `/` resolve from the directory of the file that defines that command. Pratfall performs no
shell, variable, or tilde expansion. Shell functions are not executable files on `PATH`; expose one
through a trusted executable wrapper and configure that wrapper's argv explicitly.

If the selected local path is a symlink to the global file, Prat loads it once and reports only
the selected path. Relative commands still resolve from the selected path's directory.

Built-in selectors, aliases, and management commands are reserved profile names. Inspect and
validate configuration with:

```sh
prat config path
prat --config .pratfile config init
prat --config config.toml config init
prat --config config.toml config validate
prat --config config.toml profiles
```

`config path` prints the global path unless `--config PATH` is supplied. `config init` writes to
that same target, creates a new example exclusively, and fails if the destination exists.
`config validate` checks the effective merged configuration and lists the loaded files. Its JSON
result includes `sources` in global-to-local order; `path` is the highest-precedence loaded file,
or the global path when neither file exists. Ordinary runs never write configuration.

New built-in names become reserved when upgrading. In this release, profiles named `openhands`,
`oh`, `warp`, `iflow`, `if`, `qwen`, `amp`, `reasonix`, `rx`, `droid`, `dr`, `kimi`,
`vibe`, `crush`, `cr`, `devin`, `dv`, `cortex`, `co`, or `grok` must be renamed, for example
`[profiles.oh]` to `[profiles.my-openhands]`. Keep the profile's `agent` and options, update commands that invoke its
old name, then run `prat config validate`. A collision reports
`profiles.NAME: name is reserved; choose a different profile name.`

Agents with names of four letters or fewer use their full names. Replace the former aliases
`ki` → `kiro`, `wp` → `warp`, `qw` → `qwen`, `km` → `kimi`, and `mv` → `vibe` in CLI commands
and profile `agent` fields. The former aliases are now available as custom profile names.
