# Final enhancement artifact verification

Run completed `2026-09-10T12:11:29+02:00` on macOS 26.6.2 arm64 with Python 3.13.6,
against revision `ee0a1c5e584770ca142f13628aa441efb5b7a1a6` after the final review fixes.
The coordinator executed the documented offline procedure in a fresh directory: sdist creation,
building its wheel, separate wheel and sdist installations, and installed CLI QA. Every uv command
disabled network access and Python downloads. Cached Hatchling 1.32.0 supplied the build backend.
Native commands were configured fakes with restricted PATH and isolated XDG/config paths; no real
native-agent test command ran.

The full result is `.cache/prat-qa.NSg4CQ/results.json`; the durable
[compact result](2026-09-10-enhancements-final.json) records hashes, outcomes, and cleanup.
The earlier [integration run](2026-09-10-enhancements.md) remains historical evidence, including
its Q15–Q17 ambient-PATH qualification. This fresh run uses the corrected helper for those cases.

| Check | Result |
|---|---|
| `just check` | Exit 0; 573 tests passed in 43.84s; Ruff, formatting, import cycles, and mypy clean |
| `just coverage` | Exit 0; 573 tests passed in 45.18s; 90.39% total, 85.77% branch coverage |
| `just docs-build` | Exit 0; no issues |
| `just build`, offline | Exit 0; wheel built from sdist |
| Documented offline artifact procedure | Exit 0; both distributions installed in fresh environments |
| Installed CLI scenarios | 30 passed: Q01–Q17 and E01–E12/E15 |
| Supplemental cases | All 24 documented decoder/subprocess cases passed in the final full suite |
| Runtime identity | All 25 files match source, both archives, and both installations |
| QA script identity | Both current QA scripts match the final sdist |
| Cleanup | Eight generated advisory locks available; owner-record directory empty |

E13 and E14 are explicitly marked **Not run** in the installed harness. Their passing evidence
comes from decoder/subprocess tests in the final full suite, which also covers E08's malformed and
failed accounting variants and E10's timeout/TERM-drain path. E04 executes the installed entry point
through the documented QA bootstrap and observes input-handler readiness before sending a signal;
all three readiness markers were verified. The installed package contains no test hook.

Lifecycle scenarios observe pending children and lock release before emergency fixture cleanup.
E03 independently checks each actionable diagnostic. E07 distinguishes exact timeout, overflow,
and interruption diagnostics. E11 processes 9,437,508 discarded physical JSONL bytes before
returning `STREAM_OK`. E12 observes live progress while the fake awaits release, then receives one
final stdout JSON object. E15 observes exactly two calls across a failed run and a manual fresh
rerun. The full suite additionally checks burst coalescing, idle heartbeats, late provider failures,
Copilot delta/state bounds, malformed OpenClaw provider metadata, and prompt/output cleanup.

Coverage includes 2,549 of 2,772 executable statements and 808 of 942 branches. Both the combined
measurement and branch-only measurement exceed 80%. The historical compact record's metric label
was corrected to distinguish its earlier combined measurement from branch-only coverage.

Artifact hashes:

- Wheel: `63d40b2180a87c9e1b38f0d7557f62dd2d7f93b3554c967d6f8043331be98270`
- Sdist: `5a939b87a38489d948635706126857ce7ce3757b1f3704d958fd67feba77da0d`
- Executed harness: `4ca2c4c6a3c38dd9fbc9d62c5995f3fe92c78ed24a8a1efad62e845005b15d07`
- Full result: `f770e107d9901d13629dfc3b016053b2045bbf52498d0ba0525f3924c75fca07`

This verifies wrapper behavior against protocol fixtures. It does not establish authenticated
vendor availability or replace the separately recorded optional native compatibility checks.
