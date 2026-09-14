# 0003 — Keep the runtime standard-library only

**Status:** accepted

**Recorded:** 2026-09-14

## Context

Pratfall is a wrapper that runs other people's coding agents. It has to be installable as a single
user-level tool and to run wherever those agents already run, including inside environments the user
did not build for it. Anything the runtime imports becomes a dependency of every such environment
and a candidate for a version conflict with whatever else is installed alongside it.

## Decision

Keep `dependencies = []` in [pyproject.toml](../../pyproject.toml) and import only the standard
library from `src/pratfall`. Development dependencies — pytest, pytest-cov, ruff, pylint, mypy —
are unaffected. `test_runtime_imports_only_the_standard_library` in
[test_layering.py](../../tests/test_layering.py) checks every external import name against
`sys.stdlib_module_names`, so the constraint is machine-enforced rather than conventional. The same
pull reaches tooling: the layering contract is a stdlib `ast` test rather than an added
`import-linter` dev dependency. Because no library supplies framing or back-pressure, Pratfall
bounds its own output, and every byte budget and cleanup window has one owner in
[limits.py](../../src/pratfall/limits.py) rather than being scattered as literals.

## Consequences

- The tool installs on its own and drags no dependency tree into the user's environment.
- A new third-party runtime import fails the suite instead of passing review, and the enforcement rides the pytest run that already happens.
- JSONL framing, retention accounting, process-group teardown, and a JSON parser with duplicate-key, numeric and nesting guards are written and tested here, in `consumer.py`, `runner.py` and `adapters/whole_json.py`. Their bugs are ours, and a well-maintained library would have cost less to adopt than to reimplement.
- New capability arrives only by raising the Python floor, currently 3.13; there is no compatibility shim to reach for.
