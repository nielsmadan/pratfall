# CLI enhancement installed QA

Date: 2026-09-10, completed 11:08 CEST. Platform: macOS 26.6.2 arm64. Python:
3.13.6. Runtime baseline: `2b6c5d2`, plus the Task 5 changes identified by runtime diff SHA256
`7275ce2aa8ba32f958e7eb12ce4fc08ce6dabe8d903b9daf7a71c5212eab44d6`.

This run used explicit fakes in an isolated XDG configuration. Q01–Q14 and E01–E15 limited `PATH`
to the fake directory, `/usr/bin`, and `/bin`. Q15–Q17 inherited the ambient `PATH` because their
asynchronous helper did not yet apply that restriction, but their configured commands used
absolute paths to the Python interpreter and fake harness. `HOME` was unchanged. No native agent,
inference, credentials, or global configuration was used.

The compact machine-readable result is [2026-09-10-enhancements.json](2026-09-10-enhancements.json).
The full generated result remains at `.cache/enh-integration-qa-20260910/installed-results-6.json`
for the current checkout and has SHA256
`b7126350feb8f4b4c932eba002c59e3e590a16108f020b224a031910528b977a`.

## Artifact and setup identity

| Item | Observed result |
| --- | --- |
| Wheel | `pratfall-0.1.0-py3-none-any.whl`; SHA256 `1d0fd37d3b9085af02adbd73ebbeb02459f3371a7550a3965d8aa9a9786e1bf7` |
| Sdist | `pratfall-0.1.0.tar.gz`; SHA256 `0c571e24f2d10752b14b66666df73d1fdc2dd449e4a3d65b8ad7685c76e3290f` |
| Entry points | Wheel and sdist both returned `prat 0.1.0`, exit 0 |
| Runtime contents | Source, both archives, and both installed packages contained the same 25 Python files |
| Runtime manifest | Canonical SHA256 `de89b3cd76aa6a71ad4b1f62dd6c75564a673f3192b7e3bd610a32935fa07baa` for all five copies |
| Install isolation | Both installed module roots were outside `src/` |
| Harness | Executed SHA256 `b5699410061be13f113e0d816595040206af10a25c5b40062fa13bc5d0efad9b` |

The harness verified source/archive/installed runtime identity before any scenario, then verified
the same identity again afterward. The wheel entry point ran Q01–Q17 and E01–E12/E15. The sdist
entry point supplied version and installed-runtime identity evidence only; no full sdist scenario
matrix is claimed.

The prepared sdist environment initially lacked Hatchling. The first no-build-isolation attempt
failed with `ModuleNotFoundError`, and an isolated offline retry could not resolve the backend with
`--no-index`. Installing cached Hatchling 1.32.0 and its dependencies into that prepared environment
offline allowed the final no-build-isolation sdist install to complete at exit 0. These setup
failures did not execute `prat` and are preserved in the generated check logs.

## Completed checks

| Check | Exit and complete summary |
| --- | --- |
| `just check` | Exit 0; Ruff, formatting, Pylint cycles, strict mypy, and 557 tests passed in 81.03s |
| `just coverage` | Exit 0; 557 tests passed in 81.05s; 89.95% branch-enabled coverage, above 80% |
| `just docs-build` | Exit 0; no issues; 0.27s |
| `just build` | Exit 0; sdist built, then wheel built from the sdist |
| Wheel install | Exit 0 into the prepared Python 3.13 environment |
| Sdist install | Exit 0 into the prepared Python 3.13 environment after the backend setup above |
| Installed harness | Exit 0; 30 installed passes and two intentionally delegated cases |
| Focused decoder/subprocess coverage | Exit 0; 24 tests passed in 13.87s |

## Scenario matrix

| ID | Entry and controlled state | Expected observable result | Result and evidence |
| --- | --- | --- | --- |
| Q01 | Installed help/version | Public CLI and version load | Pass — wheel entry point |
| Q02 | Both installed versions | Both report the package version | Pass — wheel and sdist entry points |
| Q03 | Built-in Claude fake | One normalized successful result | Pass — wheel entry point |
| Q04 | Named Codex profile | Profile values and argv resolve | Pass — wheel entry point |
| Q05 | Ten aliases | Every alias reaches its matching fake | Pass — wheel entry point |
| Q06 | Missing default config | Built-in selector still runs | Pass — wheel entry point |
| Q07 | Config init/validate/list | Exclusive creation and normalized output | Pass — wheel entry point |
| Q08 | Profile and CLI overrides | Precedence and native argv are exact | Pass — wheel entry point |
| Q09 | Shell-like Unicode prompt | Literal data, no shell side effect | Pass — wheel entry point |
| Q10 | Dry-run argv/stdin transports | Preview is exact and launches nothing | Pass — wheel entry point |
| Q11 | Native success/nonzero | Status, output, and exits normalize | Pass — wheel entry point |
| Q12 | Config/selector errors | Exit 2 and zero native launches | Pass — wheel entry point |
| Q13 | Missing/non-executable fake | Exit 127/126 distinction | Pass — wheel entry point |
| Q14 | Provider/protocol failure, recovery | Failures normalize; later run succeeds | Pass — wheel entry point |
| Q15 | Timeout and interruption | Pending observed; descendant locks release | Pass — wheel entry point; ambient `PATH`, absolute fake command |
| Q16 | Descendant owns output pipe | Deadline completes and lock releases | Pass — wheel entry point; ambient `PATH`, absolute fake command |
| Q17 | Stdout/stderr/input bounds | Explicit errors and owned cleanup | Pass — wheel entry point; ambient `PATH`, absolute fake command |
| E01 | Profile, CRLF Unicode file, distinct cwd | Exact bytes and independent cwd | Pass — wheel entry point |
| E02 | Automatic pipe, `-`, `--file -`, incidental pipe | Same prompt; explicit source wins | Pass — wheel entry point |
| E03 | Conflicts, missing/invalid/oversized/FIFO | Exit 2; six cases launch no fake | Pass — wheel entry point |
| E04 | Open stdin, signal before EOF | Pending input, 130/143, no child | Pass — wheel entry point; three forms |
| E05 | Fast inherit/default/profile/CLI | Exact Claude/Codex argv; unsupported rejects | Pass — wheel entry point |
| E06 | Doctor discovery and versions | Discovery launches none; probes stay per-agent | Pass — wheel entry point |
| E07 | Timeout, flooding, interruption probes | Bounded exit and descendant lock release | Pass — wheel entry point; releases observed before emergency cleanup |
| E08 | Native model and cost mappings | Requested/observed identity stays separate | Pass — installed success fixtures; failed/malformed variants from focused decoder tests |
| E09 | Repeated/distinct OpenCode steps | Replacement then distinct-step sum | Pass — wheel entry point; native cost 2.5 |
| E10 | Codex incomplete answer | Partial answer retained with failure | Pass — installed EOF case; timeout and TERM-drain from focused subprocess tests |
| E11 | Nine disposable JSONL events | Trace exceeds 8 MiB; short result succeeds | Pass — 9,437,508 physical bytes including JSON envelopes and LF; `STREAM_OK` |
| E12 | Held native completion with progress | Progress appears before release; one stdout JSON | Pass — `answering` observed while pending, then release; one stdout object |
| E13 | Framing, state, answer, encoding, precedence | Explicit bounded errors and lock cleanup | Pass — focused decoder and subprocess tests |
| E14 | Full/closed stderr and post-run hard failure | Deadline/output survives; owned children cleaned | Pass — focused subprocess tests |
| E15 | Failure followed by healthy fake | Fresh run succeeds without automatic retry | Pass — exactly two observed native invocations, one per prompt |

E08's installed fixtures cover successful reported metadata for Claude, Gemini, Copilot, and
OpenClaw. The focused decoder tests cover failed and malformed accounting and OpenCode failure
precedence. E10's installed fixture covers EOF after a completed Codex message; the focused runner
and CLI tests cover outer timeout, TERM-drain bytes, partial output, and cleanup. E13 and E14 were
not presented as installed harness passes: their pass status comes from the named 24-test focused
run in the procedure.

Q18–Q20 were not run. They remain separate optional native compatibility checks, and their
2026-09-09 records continue to describe only that historical wheel and native Codex version.

## Findings and repairs

Two integration defects were reproduced before the artifact build and repaired:

- A process starting with file descriptor 0 already closed could expose `sys.stdin` as `None` and
  escape with `AttributeError`. The repaired path returns one normalized `invalid_arguments` JSON
  result at exit 2 for implicit stdin, positional `-`, and `--file -`, without launching the fake.
  Inline and regular-file sources still succeed with startup stdin unavailable.
- Invalid native stderr UTF-8 detected after the parent exited returned the correct
  `output_encoding` result but left a same-group descendant alive. The repaired common post-run
  failure path keeps decoded output and native exit data, then cleans the owned process group.
  Lock-based subprocess tests observed release before their emergency cleanup in both progress and
  default modes.

The first five full/partial harness attempts exposed harness expectation and fixture errors rather
than further runtime defects. Their logs remain under `.cache/enh-integration-qa-20260910/checks/`.
The final run used a fresh `run-6` consumer directory and completed at exit 0. All 47 advisory-lock
files created across the verification attempts were acquirable afterward, and no harness owner
records remained. No bugs remain open from this run.

## Conclusion

All 32 required offline scenarios passed through installed runtime evidence and supplemental
decoder/subprocess evidence together. The installed matrix is complete for the wheel artifact;
the sdist evidence is limited to archive/runtime identity and its installed entry point. The run
does not establish native vendor availability, credentials, account eligibility, pricing, or the
unverified Antigravity stdin-stream timeout contract.
