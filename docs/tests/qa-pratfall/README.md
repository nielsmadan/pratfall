# Installed consumer QA

This procedure checks a built Pratfall distribution through the installed `prat` entry point. Run
it before a release or after changes to CLI parsing, configuration, adapters, normalization, or
process lifecycle behavior. It uses fake native executables and spends no inference credits.

## Prerequisites and inputs

- Python 3.13, uv, and the repository development environment.
- A clean, unique directory under `.cache/` for two isolated environments and consumer state.
- A Git revision that identifies the runtime baseline.
- Existing authenticated Codex only for the three separately controlled live checks.

The harness is [scripts/qa_installed.py](../../../scripts/qa_installed.py). Its fake executables
emit the protocol shapes recorded in `tests/test_run_cli.py`; they do not import Pratfall. The
consumer working directory and both environments remain outside `src/`.

## Offline procedure

From the repository root, choose a new `qa_root` and run:

```sh
qa_root=$(mktemp -d .cache/prat-qa.XXXXXX)
mkdir -p "$qa_root/artifacts" "$qa_root/consumer"
uv build --sdist --out-dir "$qa_root/artifacts"
sdist=$(find "$qa_root/artifacts" -name '*.tar.gz' -type f)
uv build --wheel "$sdist" --out-dir "$qa_root/artifacts"
wheel=$(find "$qa_root/artifacts" -name '*.whl' -type f)
uv venv --python 3.13 "$qa_root/env-wheel"
uv pip install --python "$qa_root/env-wheel/bin/python" "$wheel"
uv venv --python 3.13 "$qa_root/env-sdist"
uv pip install --python "$qa_root/env-sdist/bin/python" "$sdist"
uv run python scripts/qa_installed.py \
  --prat "$qa_root/env-wheel/bin/prat" \
  --sdist-prat "$qa_root/env-sdist/bin/prat" \
  --wheel "$wheel" \
  --sdist "$sdist" \
  --expected-version "$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)" \
  --base-revision "$(git rev-parse HEAD)" \
  --work-dir "$qa_root/run" \
  --output "$qa_root/results.json"
```

Read the complete exit status and result file. Preserve a compact copy with a new dated run record
when the run is release evidence. Do not retain the generated multi-megabyte stdout/stderr streams;
the harness records their byte counts, hashes, normalized errors, pending observation, and
descendant-lock release.

## Controlled live Codex checks

Use one installed wheel, native Codex `gpt-5.6-luna`, low effort, ephemeral sessions, an explicit
wall deadline, and no retries or fallback. Run response-only prompts with `--sandbox read-only`.

1. Request exactly `PRAT_TEXT_OK` and prohibit tools, delegation, and file access.
2. Pipe a multiline request for exactly `PRAT_JSON_OK` through the `simple` profile with `--json`.
3. In a disposable directory containing only `greeting.txt`, request the exact one-line change and
   `PRAT_FILE_OK` with `--sandbox workspace-write`; inspect file content and directory membership.

Record the complete Prat argv, prompt or stdin, working directory, native version, result stdout and
stderr, return code, duration, usage when reported, artifact hash, and resulting files. Do not infer
a recovery mechanism from normalized output when native events were not retained.

## Evaluation and cleanup

Every Q01–Q17 assertion must pass. Pending process evidence requires that `prat` is still running
while a same-group descendant holds an exclusive advisory lock; cleanup requires acquiring that
lock after completion. Q18–Q20 require exact output or file content through a real native CLI. Run
`just check`, `just coverage`, `just docs-build`, `just build`, and `git diff --check` after records
and docs are complete.

Remove only the generated QA directory after its useful evidence has been captured. The harness
uses a disposable XDG config directory, restores no global state, and leaves no native sessions.

## Recorded runs

- [2026-09-09 initial release QA](runs/2026-09-09-initial.md)
- [2026-09-09 harness repair and installed retest](runs/2026-09-09-repair.md)
- [2026-09-09 verification after final review fixes](runs/2026-09-09-final-review.md)
