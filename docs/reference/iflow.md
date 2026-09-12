# iFlow 0.5.19

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The [published npm metadata](https://registry.npmjs.org/@iflow-ai/iflow-cli/0.5.19)
identifies `bundle/entry.js`, which loads `bundle/iflow.js`. The downloaded package's SHA-1 is
`d406e81748593c37ef464ff99cc5b495d77a76ce`; its bytes were inspected without installing or
executing them. Its bundled CLI declares `prompt`/`p` and `model`/`m` as string options. The
bundled parser handles `--KEY=VALUE` using a suffix match accepting newlines (`[\s\S]*`), and the
CLI's own prompt preprocessing converts dash-leading split prompt arguments to equals form.
This establishes exact `--prompt=PROMPT`, empty stdin and optional `--model VALUE`, including
Unicode, leading dashes, literal shell syntax and newlines. OS argv limits still apply.

Prat captures bounded native stdout, removing only terminal CR/LF. It cannot promise final-only
text or infer provider failures from prose; native exit status decides success. Accepted native
flags `--thinking`, `--plan`, and `--default` each take zero values. These are native modes, not a
generic effort ladder. Prompt/interactive/continue/resume, model, output/file/stream/ACP/server,
config/cwd and budget/timeout selectors are reserved or rejected. No normalized budgets are
exposed in this initial adapter.

Static configuration assembly resolves approval mode from native settings first, then explicit
modes; otherwise a noninteractive prompt selects `YOLO`. Thus the pinned package's default is
verified automatic approval, replacing the earlier uncertainty. `--default` and `--plan` can be
chosen explicitly through native arguments; Prat adds neither an approval mode nor a bypass.
Native startup can update its own settings; Prat does not perform or suppress that native behavior.
The [official retirement FAQ](https://vibex.iflow.cn/t/topic/4819) dates maintenance end to
2026-03-20 and hosted service shutdown to 2026-04-17, and confirms existing installations can
continue with custom APIs. Existing BYOK configuration is required; Prat does not migrate it.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/iflow.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
