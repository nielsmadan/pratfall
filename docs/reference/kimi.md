# Kimi CLI 1.50.0

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The pinned [parser](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/cli/__init__.py)
establishes `kimi --print --input-format text --output-format stream-json --final-message-only`
with plain stdin and optional `--model`. Native stdin is trimmed. Accepted native options are
`--thinking`, `--no-thinking`, `--plan`, and `--debug` (zero values). Thinking is boolean, so
there is no generic effort mapping. Prompt/command, print/quiet/input/output, model, config/work-dir,
session/resume/continue, ACP/wire/remote and administrative selectors are reserved or rejected.
Version probing uses `--version`. Print mode implies AFK: tools are automatically approved and
questions dismissed. Prat adds no approval flag. Support targets this kimi-cli version; the
successor kimi-code is not substituted.

The [final-only printer](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/ui/print/visualize.py)
emits JSONL with role assistant and string content, without a type or terminal-result marker.
Prat keeps the latest assistant string; native exit zero plus EOF completes, including empty
stdout because the printer suppresses empty final text. Step changes clear native buffered text;
Prat can retain only text actually emitted. Repeated valid assistant messages replace earlier ones.
Malformed records fail while retaining earlier text. [Print errors](https://github.com/MoonshotAI/kimi-cli/blob/1.50.0/src/kimi_cli/ui/print/__init__.py)
can write plain text before native nonzero exit; native status remains authoritative. JSONL bounds
apply. Model/accounting fields remain unknown. Source review preceded independent fixtures.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/kimi.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
