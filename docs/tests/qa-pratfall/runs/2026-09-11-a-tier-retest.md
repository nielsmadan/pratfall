# A-tier installed package retest

Completed `2026-09-11T03:04:38.671699+02:00` on macOS 26.6.2 arm64, Python 3.13.6. This exercises the twelve-agent
extension through installed `prat` entry points using strict fake native executables. Runtime
revision: `98a615b59ed656a8f9dca1934341763b3b7e3832`; its runtime diff is empty. Task 5 harness
and guide edits were still uncommitted; artifact and script hashes below identify the exact build.

The [offline procedure](../README.md) ran in fresh `.cache/prat-a-tier-qa._o3mgaks` environments
using cached Hatchling 1.32.0. A wheel was built from the sdist, then both artifacts were installed
separately. Every uv command disabled networking and Python downloads. No native agent executable,
authentication flow or user-global configuration was used. The [command log](2026-09-11-a-tier-retest.log) preserves
setup and invocation; the [complete bounded result](2026-09-11-a-tier-retest.json) preserves all 64 scenario records,
per-artifact A observations, exact inputs/argv, hashes, and runtime manifests.

## Observed matrix

The complete scenario inputs and expected outcomes are in the procedure and executed harness;
these rows map the approved A01-A18 acceptance matrix to actual observations.

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

The installed harness reports **62 Pass, 2 Not run**: Q01-Q17, E01-E12/E15, and 32 focused A
scenarios. Each A scenario has independent wheel and sdist observations (64 artifact observations).
E13 and E14 are explicitly Not run through artifacts; their decoder/subprocess tests passed in the
full source suite. The broader numeric/nesting/nonfinite/Unicode and every-byte/state-bound
matrices are source-test evidence. Optional credentialed Q18-Q20 are outside this offline scope.

## Checks and finding closure

| Check | Observed result |
| --- | --- |
| Root `just check` | Exit 0; 1397 passed in 69.58s; Ruff, format, Pylint and strict mypy passed |
| Root `just coverage` | Exit 0; 1381 passed in 67.93s; 92.77% combined and 88.98% branch coverage |
| Root `just docs-build` | Exit 0; no issues |
| Root `just build` | Exit 0; wheel built from sdist |
| Fresh offline build/install/QA | Exit 0; 62 passing scenarios, with E13/E14 source-only |
| Identity | 38 runtime files match all five manifests; both current QA scripts match sdist |
| Cleanup | Eight advisory locks released; ownership directory empty |

Coverage was measured against the same committed runtime before the 16 harness-dispatch regression
cases were added. It covers 3450/3666 statements and 1146/1288 branches. The later full check includes
those cases; both measurements exceed the required coverage floor.

The [initial failed run](2026-09-11-a-tier-initial.md) stopped at E08 because the new A fixture
prefix also matched old accounting controls. The repaired dispatcher uses the numbered fixture
namespace; five legacy accounting cases, ordinary boundaries and unknown controls now have source
regressions. E08 and E09 pass in this fresh installed run. No product accounting change was needed.

A separately reproduced process-cleanup race was corrected in `98a615b`: transient zero-signal
EPERM remains pending, while persistent uncertainty and TERM/KILL failures remain explicit within
bounded waits. Root and independent probes cover both cleanup paths; lifecycle artifact cases pass.

Artifact SHA-256 values:

- Wheel: `6353dedc8c27b5a0fabc9c5a96fb9905d60f1636d19aab909e276aa73830c630`
- Sdist: `f9b6359f2393e89ae327d1b9ae6a2b09cbac4710b0ee5056b572ed83bda84591`
- Executed harness: `1c4e8ba734dac2b3e4be107f4c5377843b78dae49c1eea9f52ae020c5e86e775`
- Original full result: `63eade4ec9df674bdef02583ecb8eece7477af466796ccd195f2f49bf4f0826b`

This establishes wrapper behavior against version-scoped native protocol fixtures. Authenticated
vendor availability and compatibility with later CLI versions remain outside this run. Disposable
QA directories are retained until the final review completes; cleanup observations above were made
before any directory removal.
