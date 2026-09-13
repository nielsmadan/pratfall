# Getting started

Pratfall requires Python 3.13 or newer. It also requires the native CLI for each agent you intend to
use. Pratfall does not install agents, log in, edit native configuration, or fall back to another
agent.

Until a release is published, install or run from a source checkout:

```sh
uv sync
uv run prat --help
uv run prat doctor
uv run prat doctor --versions
uv run prat cx "summarize this checkout"
```

For repository development, `just setup` installs all dependency groups and Lefthook repository
hooks. `just install` installs or replaces the current source as a user-level uv tool, including at
the same version, and `just install-editable` links the source instead. Both are explicit machine
changes. Use `just uninstall` to remove that tool installation.

`prat doctor` only checks whether configured executable names resolve on `PATH`. It does not run an
agent or verify credentials. `prat doctor --versions` opts into executing each available configured
command prefix with its version arguments (`version` for Amp, `--version` for others); trusted wrappers may have side effects. Each probe gets empty stdin,
a three-second deadline, and separate 64 KiB stdout and stderr limits. Probe errors are inventory
data, while interruption stops the remaining probes. Neither command checks authentication.
Install and authenticate each native CLI from its official instructions before use.
