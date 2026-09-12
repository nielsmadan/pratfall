# Adapter reference

Each adapter exposes three operations: validate trusted native arguments, build an immutable
invocation, and decode complete bounded stdout after the process exits. `registry.py` is the only
adapter assembly point.

Structured adapters require verified completion evidence and treat semantic provider failures as
errors even when the process exits zero. Text adapters return bounded native stdout with terminal
line endings removed and cannot separate a final answer from native banners or progress.

Capability metadata describes whether an adapter accepts `model`, `effort`, `fast`, and a small set of
native budgets. It does not catalog provider model names. The normalized `model` field records the
resolved requested model, or null when the native default was selected.

Claude and Codex are the only adapters with verified invocation-only fast mappings. Their native
settings/config flags remain reserved so `native_args` cannot replace the resolved Pratfall value.
Each catalog entry also carries its version-probe arguments; all current entries use `--version`.

`native_args.py` accepts a finite per-adapter set of known flags with explicit arity. It rejects
response files, positional arguments, subcommands, and fields owned by Pratfall. A trusted wrapper
is the escape hatch for other native options. See [agent evidence](agent-evidence.md) for native
versions, schemas, and source links.
