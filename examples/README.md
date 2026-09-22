# Configuration examples

[config.toml](config.toml) demonstrates defaults, named profiles, profile inheritance with
`extends`, and custom commands, including a trusted wrapper with fixed arguments.

## Try the example

Replace the example model IDs and wrapper path with values valid on your machine, then inspect
the configuration and preview a run:

```sh
prat --config examples/config.toml config validate
prat --config examples/config.toml profiles
prat --config examples/config.toml simple "review this change" --dry-run
```

`simple` is a profile defined in the example file. `--dry-run` previews the command without
launching the agent.

## Save your settings

Copy the sections you need into your project's `.pratfile`, or into the global file shown by
`prat config path`. See [profiles and configuration](../docs/user/profiles.md) for merging rules.

The wrapper path in the example is a placeholder. To use a shell function, expose it through a
trusted executable wrapper, then put the wrapper command and fixed arguments in the `command`
array.
