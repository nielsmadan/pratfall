# Architecture

`prat` follows a one-way flow:

1. `cli.py` separates management and run syntax, loads configuration, and resolves one prompt.
2. `config.py` validates all profiles and applies invocation, profile, and default precedence.
3. `catalog.py` supplies immutable agent metadata and capability checks.
4. The selected module in `adapters/` builds an argv/stdin invocation.
5. `runner.py` starts a process group, drains bounded stdout and stderr concurrently, and enforces
   one monotonic deadline.
6. The adapter decodes native output and `output.py` applies exit/error precedence to one normalized
   result.

The adapter registry binds small command builders and post-exit decoders. The runner does not know
provider schemas, and adapters do not own process lifecycle. The standard-library-only runtime
keeps source and Homebrew installation free of vendored Python resources.

stdout is reserved for final text or one JSON object. Native stderr and Pratfall progress go to
stderr. Child stdin is always explicit bytes or closed; it never inherits the user's terminal.
