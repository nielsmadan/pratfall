# Installed consumer QA

This procedure checks a built Pratfall distribution through the installed `prat` entry point. Run
it before a release or after changes to CLI parsing, configuration, adapters, normalization, or
process lifecycle behavior. It uses fake native executables and spends no inference credits.

## Prerequisites and inputs

- A locally available Python 3.13 interpreter, uv, and the already-synced repository development
  environment.
- Cached distributions for Hatchling 1.32.0 and all of its build dependencies. The sdist build
  and install below deliberately use that cached backend without build isolation.
- A clean, unique directory under `.cache/` for two isolated environments and consumer state.
- A Git revision that identifies the runtime baseline.

The harness is [scripts/qa_installed.py](../../../scripts/qa_installed.py). Its fake executables
emit the protocol shapes recorded in `tests/test_run_cli.py`; they do not import Pratfall. The
consumer working directory and both environments remain outside `src/`.

For E04, a QA-only bootstrap uses the installed environment's Python interpreter to execute the
installed `prat` entry-point file. It observes Python signal-handler installation and writes a
readiness marker only after `pratfall.prompt_input` has installed its SIGTERM handler. The harness
waits a bounded time for that marker before sending SIGINT or SIGTERM, so the recorded pending
observation covers the input handler rather than an assumed startup delay. This instrumentation is
confined to the harness; the installed package has no test hook.

## Offline procedure

From the repository root, choose a new `qa_root` and run. Every uv command disables network access
and Python downloads; `uv run --no-sync` also prevents an implicit environment update:

```sh
qa_root=$(mktemp -d .cache/prat-qa.XXXXXX)
mkdir -p "$qa_root/artifacts" "$qa_root/consumer"
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/build-env"
uv pip install --offline --no-python-downloads \
  --python "$qa_root/build-env/bin/python" "hatchling==1.32.0"
uv build --offline --no-python-downloads --no-build-isolation \
  --python "$qa_root/build-env/bin/python" --sdist --out-dir "$qa_root/artifacts"
sdist=$(find "$qa_root/artifacts" -name '*.tar.gz' -type f)
uv build --offline --no-python-downloads --no-build-isolation \
  --python "$qa_root/build-env/bin/python" --wheel "$sdist" --out-dir "$qa_root/artifacts"
wheel=$(find "$qa_root/artifacts" -name '*.whl' -type f)
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/env-wheel"
uv pip install --offline --no-python-downloads --no-deps \
  --python "$qa_root/env-wheel/bin/python" "$wheel"
uv venv --offline --no-python-downloads --python 3.13 "$qa_root/env-sdist"
uv pip install --offline --no-python-downloads \
  --python "$qa_root/env-sdist/bin/python" "hatchling==1.32.0"
uv pip install --offline --no-python-downloads --no-build-isolation --no-deps \
  --python "$qa_root/env-sdist/bin/python" "$sdist"
uv run --offline --no-python-downloads --frozen --no-sync python scripts/qa_installed.py \
  --prat "$qa_root/env-wheel/bin/prat" \
  --sdist-prat "$qa_root/env-sdist/bin/prat" \
  --wheel "$wheel" \
  --sdist "$sdist" \
  --expected-version "$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)" \
  --base-revision "$(git rev-parse HEAD)" \
  --work-dir "$qa_root/run" \
  --output "$qa_root/results.json"
```

An unavailable interpreter or distribution is a failed prerequisite. Stop and reconnect, then use
a separate preparation directory to populate the project and build-backend caches:

```sh
uv sync --frozen
prep_root=$(mktemp -d .cache/prat-qa-prep.XXXXXX)
uv venv --python 3.13 "$prep_root"
uv pip install --python "$prep_root/bin/python" "hatchling==1.32.0"
```

After preparation, discard the partial `qa_root` and restart the offline procedure in a new
directory. Keep the offline flags in place so another missing cache entry stops rather than
silently contacting an index.

Read the complete exit status and result file. Preserve a compact copy with a new dated run record
when the run is release evidence. Do not retain the generated multi-megabyte stdout/stderr streams;
the harness records their byte counts, hashes, normalized errors, pending observation, and
descendant-lock release.

The wheel entry point runs Q01–Q17 and the core E01–E12/E15 enhancement paths. Both wheel and sdist
entry points are version-checked, and source, archive, and installed runtime manifests must match.
Focused decoder and subprocess tests supplement E08's malformed/failure variants and E10's
timeout/TERM-drain variant, and cover the adversarial byte-transport cases E13 and E14:

```sh
uv run --offline --no-python-downloads --frozen --no-sync pytest \
  tests/test_adapters.py::test_claude_provider_failure_outranks_malformed_accounting \
  tests/test_adapters.py::test_gemini_malformed_response_preserves_valid_reported_models \
  tests/test_adapters.py::test_copilot_terminal_error_beats_protocol_error_and_preserves_text \
  tests/test_adapters.py::test_openclaw_provider_failure_preserves_accounting_and_outranks_malformed_cost \
  tests/test_adapters.py::test_opencode_provider_failure_outranks_malformed_cost \
  tests/test_adapters.py::test_jsonl_consumers_accept_every_byte_boundary_and_final_record \
  tests/test_adapters.py::test_jsonl_event_bound_counts_blank_unknown_and_crlf_records \
  tests/test_adapters.py::test_jsonl_bounds_retained_records_and_answer_join_separators \
  tests/test_adapters.py::test_jsonl_rejects_unpaired_surrogate_excessive_nesting_and_numeric_width \
  tests/test_adapters.py::test_late_provider_failures_outrank_duplicate_terminal_protocol_errors \
  tests/test_runner.py::test_finish_time_limit_cleans_descendant_and_preserves_decoded_output \
  tests/test_runner.py::test_timeout_and_interruption_feed_term_cleanup_bytes_to_consumer \
  tests/test_run_cli.py::test_codex_outer_timeout_preserves_completed_message_and_cleans_fake \
  tests/test_run_cli.py::test_progress_full_stderr_before_launch_does_not_block_or_change_flags \
  tests/test_run_cli.py::test_progress_full_stderr_during_execution_does_not_stall_deadline_or_cleanup \
  tests/test_run_cli.py::test_progress_final_stderr_failure_preserves_output_and_cleans_owned_descendant \
  tests/test_run_cli.py::test_invalid_native_stderr_cleans_owned_descendant_after_parent_exit \
  tests/test_run_cli.py::test_progress_with_closed_stderr_preserves_normalized_json_and_does_not_launch \
  --basetemp=.cache/prat-qa-focused
```

## Optional native compatibility checks

Q18–Q20 are separate credentialed compatibility checks. They are not part of the required offline
enhancement procedure, and old results apply only to their recorded artifact and native version.
When deliberately repeating them, use one installed wheel, native Codex `gpt-5.6-luna`, low effort,
ephemeral sessions, an explicit wall deadline, and no retries or fallback. Run response-only
prompts with `--sandbox read-only`.

1. Request exactly `PRAT_TEXT_OK` and prohibit tools, delegation, and file access.
2. Pipe a multiline request for exactly `PRAT_JSON_OK` through the `simple` profile with `--json`.
3. In a disposable directory containing only `greeting.txt`, request the exact one-line change and
   `PRAT_FILE_OK` with `--sandbox workspace-write`; inspect file content and directory membership.

Record the complete Prat argv, prompt or stdin, working directory, native version, result stdout and
stderr, return code, duration, usage when reported, artifact hash, and resulting files. Do not infer
a recovery mechanism from normalized output when native events were not retained.

## Evaluation and cleanup

Every Q01–Q17 assertion and installed core E scenario must pass. Pending process evidence requires
that `prat` is still running while a same-group descendant holds an exclusive advisory lock;
cleanup requires acquiring that lock after completion and before the harness's emergency cleanup.
E04 also requires installed-entry-point signal readiness before the harness sends its signal. E11
counts complete physical JSONL records, including envelopes and line delimiters. E12 requires
observing progress while the controlled native process is still waiting for release. E13 and E14
pass only when the named focused decoder and subprocess tests pass; label that evidence separately from the
installed run. Optional Q18–Q20 require exact output or file content through a real native CLI. Run
`just check`, `just coverage`, `just docs-build`, `just build`, and `git diff --check` after records
and docs are complete.

Remove only the generated QA directory after its useful evidence has been captured. The harness
uses a disposable XDG config directory, restores no global state, and leaves no native sessions.

## Recorded runs

- [2026-09-09 initial release QA](runs/2026-09-09-initial.md)
- [2026-09-09 harness repair and installed retest](runs/2026-09-09-repair.md)
- [2026-09-09 verification after final review fixes](runs/2026-09-09-final-review.md)
- [2026-09-10 CLI enhancement installed QA](runs/2026-09-10-enhancements.md)
- [2026-09-10 final enhancement artifact verification](runs/2026-09-10-enhancements-final.md)
