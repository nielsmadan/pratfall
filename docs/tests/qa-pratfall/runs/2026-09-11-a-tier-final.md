# Final A-tier artifact verification

Completed `2026-09-11T03:28:00.551229+02:00` on macOS 26.6.2 arm64 with Python 3.13.6, against committed runtime
`abc330ec6d1c2b93b4da2edbd098101c1d521d81`. The runtime diff is empty. This final run follows the
independent review corrections to accounting retention, shared JSON parsing and optimized-mode QA
refusal. Artifact and harness hashes identify the tested files; later record-only changes do not
change their runtime identity.

Root executed the [offline procedure](../README.md) in fresh `.cache/prat-a-tier-qa.dw7q3mtb`
environments with cached Hatchling 1.32.0. The wheel was built from the sdist and each artifact was
installed separately. Every uv invocation disabled networking and Python downloads. Native commands
were strict fakes; no real agent, authentication flow, global installation or user configuration
change occurred. The [command log](2026-09-11-a-tier-final.log) and [bounded result](2026-09-11-a-tier-final.json) preserve exact
setup, inputs/argv, all scenario statuses, per-artifact observations and runtime manifests.

## Final observed matrix

| ID | Priority | Observed evidence and boundary | Result |
| --- | --- | --- | --- |
| A01 | Core | Both artifacts: exact 22-agent inventory/capabilities; ordinary doctor launches none. | Pass |
| A02 | Core | Both artifacts: all 22 canonical names and 22 aliases launch exactly once. | Pass |
| A03 | Core | Both artifacts: Qwen alias profile, literal prefix, overrides, cwd and dry-run. | Pass |
| A04 | Core | Both artifacts: Devin argv and Qwen stdin via inline/file/pipe/explicit dash forms. | Pass |
| A05 | Core | Both artifacts: A04.sources preserves CRLF, Unicode, dash-leading input and paths with spaces. | Pass |
| A06 | Core | Both artifacts: unsupported controls fail before launch; A03.profile verifies dry-run. | Pass |
| A07 | Core | Both artifacts: newly reserved profile names fail with actionable diagnostics. | Pass |
| A08 | Core | Both artifacts: all 22 exact version probes, including Amp version and isolated failure. | Pass |
| A09 | Core | Both artifacts: verified structured failures and Vibe assistant projection; all routes cover success. | Pass |
| A10 | Core | Both artifacts: Qwen child error followed by root success; child text/models excluded. | Pass |
| A11 | Core | Both artifacts: Reasonix paused native-zero failure and known tokens; false accounting aliases remain null. | Pass |
| A12 | Core | Both artifacts: OpenHands mixed framing/recovery, conversation failure and missing terminal. | Pass |
| A13 | Core | Both artifacts: Kimi/Warp empty completion and native failure with partial text. | Pass |
| A14 | Core | Both artifacts: Droid numeric width, Reasonix nesting and Vibe Unicode; full hostile matrices pass in source tests. | Pass |
| A15 | Core | Both artifacts: iFlow/Crush/Devin/Cortex complete text and native status preserved. | Pass |
| A16 | Core | Wheel Q15-Q17/E07/E10-E11 observe lifecycle, limits and partial output; source tests supplement framing/state bounds. | Pass |
| A17 | Core | Wheel E12 observes progress while native fake waits, then one stdout JSON object; source tests supplement backpressure. | Pass |
| A18 | Core | 38 runtime files match source, both archives and both isolated installs; Q01-Q17 and core E scenarios pass. | Pass |

The installed harness reports **62 Pass, 2 Not run**: Q01-Q17, E01-E12/E15, and 32 A scenarios.
Every A scenario has separate passing wheel and sdist observations. E13/E14 remain source-only
adversarial decoder/subprocess cases and passed in the full suite. Per-agent every-byte, numeric,
nesting, nonfinite, Unicode and state-bound matrices are automated source evidence; their entire
cross product is not claimed as installed coverage. Optional credentialed Q18-Q20 were not run.

## Final checks

| Check | Result |
| --- | --- |
| Root `just check` | Exit 0; 1408 passed in 71.06s; Ruff, format, Pylint and strict mypy green |
| Root `just coverage` | Exit 0; 1408 passed in 72.28s; 92.73% combined, 88.92% branch |
| Root `just docs-build` | Exit 0; no issues |
| Fresh offline build/install/QA | Exit 0; all required installed scenarios pass |
| Runtime identity | All 38 files match source, both archives and both installed packages |
| Harness identity | Both current QA scripts match the sdist |
| Cleanup | Eight advisory locks available, zero ownership records, three input-readiness markers |

Coverage includes 3420/3636 statements and 1131/1272 branches. Both coverage measurements exceed
the 80% requirement. The full suite includes regressions for valid usage beside malformed terminal
text, bounded accounting replacement, and `-O`/`PYTHONOPTIMIZE` refusal before harness/fake work.
Independent scoped re-review checked 1000 accounting replacements per adapter, seven Qwen
precedence sequences, 93 Reasonix comparisons and both optimized entry modes; no residual issue
was found in those corrections.

The [initial failure](2026-09-11-a-tier-initial.md) and [first successful retest](2026-09-11-a-tier-retest.md)
remain historical. The initial fixture-prefix collision is fixed; E08/E09 accounting checks pass
here with the final harness. Native timeout/cancellation/progress cases observe pending work and
lock release before emergency fixture cleanup. At capture, the remaining changes in this run are verification records.

Artifact SHA-256 values:

- Wheel: `991c84adbcc9a36fbf06b698247a275b275a50dbf7951d4e94a6a6ba56c32105`
- Sdist: `c9125026bafd715dfd1f099fb9ec9c668e8f6b3961590711637aad683cb58ebf`
- Executed harness: `d348ec00914ae54bb324e7b3a955aa52792061f28d4d02f75cabdd4305a3b85d`
- Original full result: `72939f1d4b56e670109d08b5cabc27c7cca5099b13b971859830f17bc80537ff`

This establishes wrapper behavior against the documented version-scoped native protocols. Live
vendor availability and compatibility with later native versions remain outside the offline scope.
Disposable QA directories are removed only after preserving these records and checking ownership.

Final cleanup: all three owned QA directories were removed after their eight locks were
rechecked and ownership records found empty. `just build` also passed after record creation;
both distributions in `dist/` match the tested runtime manifest.
