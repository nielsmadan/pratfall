# Configuration examples

`config.toml` shows defaults, profiles, a normal executable, and an executable argv prefix for a
trusted wrapper. Copy the relevant sections to the path printed by `prat config path`, then replace
the example model IDs and wrapper path with values valid on your machine.

Validate before running an agent:

```sh
prat --config examples/config.toml config validate
prat --config examples/config.toml profiles
prat --config examples/config.toml simple "review this change" --dry-run
```

The wrapper entry is intentionally an absolute placeholder. Shell functions are not available as
`PATH` executables; create a trusted executable wrapper and place its command plus fixed arguments
in the `command` array.
