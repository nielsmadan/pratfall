# Getting started

Pratfall requires Python 3.13 or newer. It also requires the native CLI for each agent you intend to
use. Pratfall does not install agents, log in, edit native configuration, or fall back to another
agent.

Until a release is published, install or run from a source checkout:

```sh
uv sync
uv run prat --help
uv run prat doctor
uv run prat cx "summarize this checkout"
```

For repository development, `just setup` installs all dependency groups and Lefthook repository
hooks. `just install-local` installs the current source as a user-level uv tool, and
`just refresh-local` performs a no-cache reinstall. Both are explicit machine changes. Use
`just reset-local` to remove that tool installation.

`prat doctor` only checks whether configured executable names resolve on `PATH`. It does not run an
agent or verify credentials. Install and authenticate each native CLI from its official
instructions before use.
