# Amp

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The [streaming schema](https://ampcode.com/docs/cli/streaming-json), checked 2026-09-11,
establishes `amp --execute --stream-json` with plain stdin. Only `--stream-json-thinking` (zero
values) is accepted as an optional native flag. Input/output, model, config/cwd, thread/resume,
executor/orb/runner and administrative selectors are reserved or rejected. Generic model, effort,
fast and budgets are unsupported; native mode is not a model identifier. The documented
[version probe](https://ampcode.com/docs/cli) is `amp version`.

Amp's last message is one result: success/false with a result string, or
error_during_execution/error_max_turns/true with an error string. Matching system errors also
fail. Duplicate results and records after a result are protocol errors. Root assistant text can
survive failure; child text, tool, thinking and redacted-thinking blocks are excluded. Optional
result usage maps input/output/cache-read/cache-creation counts; absent accounting stays null,
without summing assistant snapshots. Reported models and USD cost remain unknown. JSONL bounds
apply. Fixtures were accepted only after this schema review.

[Execute mode](https://ampcode.com/docs/cli/execute-mode) inherits native authentication. The
[current permission default](https://ampcode.com/news/neo) executes tools without prompts unless
existing native settings enable permissions. Prat adds no bypass and performs no native setup.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/amp.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
