# Profiles and configuration

The default config is `$XDG_CONFIG_HOME/pratfall/config.toml`, falling back to
`~/.config/pratfall/config.toml`. An empty `XDG_CONFIG_HOME` uses the fallback. `--config PATH`
selects another file, with relative paths resolved from the invocation directory.

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

A profile requires `agent`. It may set `model`, `effort`, `fast`, `timeout`, `native_args`, and
agent-backed budget fields. Invocation flags override profiles, which override defaults. Native argument arrays
replace the lower-precedence array. Profiles do not inherit from each other.

`fast` is an optional boolean for Claude and Codex. `true` and `false` are both explicit overrides;
when absent, the native setting remains in force. A global default is validated against a built-in
agent only when that selector is resolved, while every configured profile is validated after merging
defaults. Unsupported agents reject either boolean value.

`[agents.NAME].command` accepts an executable argv prefix. Bare executable names use `PATH`.
Relative executable paths containing `/` resolve from the config directory. Pratfall performs no
shell, variable, or tilde expansion. Shell functions are not executable files on `PATH`; expose one
through a trusted executable wrapper and configure that wrapper's argv explicitly.

Built-in selectors, aliases, and management commands are reserved profile names. Inspect and
validate configuration with:

```sh
prat config path
prat --config config.toml config init
prat --config config.toml config validate
prat --config config.toml profiles
```

`config init` creates a new example exclusively and fails if the destination exists. Ordinary runs
never write configuration.

New built-in names become reserved when upgrading. In this release, profiles named `openhands`,
`oh`, `warp`, `wp`, `iflow`, `if`, `qwen`, `qw`, `amp`, `reasonix`, `rx`, `droid`, `dr`, `kimi`,
`km`, `vibe`, `mv`, `crush`, `cr`, `devin`, `dv`, `cortex`, or `co` must be renamed, for example
`[profiles.oh]` to `[profiles.my-openhands]`. Keep the profile's `agent` and options, update commands that invoke its
old name, then run `prat config validate`. A collision reports
`profiles.NAME: name is reserved; choose a different profile name.`
