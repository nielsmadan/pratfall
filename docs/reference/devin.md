# Devin 3000.10.21 baseline

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The [command reference](https://docs.devin.ai/cli/reference/commands), checked 2026-09-11,
establishes `devin -p -- PROMPT`: the entire UTF-8 prompt is one literal argv item, with empty
stdin. `--model VALUE` and the optional native `--permission-mode VALUE` precede the delimiter.
The native permission option has arity one and is never injected. Prompt-file/stdin markers are
not inferred; OS argv-size limits apply. Print/prompt/file/output/export, model, config/cwd,
continue/resume, trust-bypass, cloud/remote/executor and administrative controls are reserved or
rejected. No generic effort, fast or budget controls are verified. Version probing uses `--version`.

Print requires an already trusted workspace and existing native authentication. The
[permission reference](https://docs.devin.ai/cli/reference/permissions) describes configurable
tool approvals, but does not establish every print-mode approval outcome. Prat retains the native
behavior and makes no stronger claim. It captures bounded native stdout, removing only terminal
CR/LF; native exit status determines success, including empty successful output. Generated error
prose has no semantic meaning to the decoder. Usage, reported models and cost remain null.
These public contracts were frozen before accepting fixtures; no native CLI was executed.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/devin.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
