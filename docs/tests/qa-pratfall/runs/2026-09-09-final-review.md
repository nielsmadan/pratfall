# Verification after final review fixes

Date: 2026-09-09. Platform: macOS 26.6.2 arm64, Python 3.13.6. Baseline: `0323ef6`,
plus the final-review working-tree changes identified by the runtime manifest in
[the final installed result](2026-09-09-handoff-retest.json).

Root independently ran all checks below. No live agent invocation was used for
this verification. The earlier controlled Codex checks remain historical evidence
for their recorded wheel; they are not claimed to use these final artifact bytes.

| Check | Result |
| --- | --- |
| `just check` |422 passed in 39.78s; Ruff, formatting, Pylint cycles and strict mypy passed|
| `just coverage` |419 passed in 39.06s; 91.73% with branches enabled; final runtime unchanged|
| `just docs-build` |No issues; strict build completed in 0.25s|
| Actionlint 1.7.12 with ShellCheck 0.11.0 |Exit 0 across all workflows|
| Sdist, then wheel built from that sdist |Both built and installed in fresh environments|
| Installed Q01–Q17 |All 17 passed; full result and runtime identity linked above|
| Formula resource verification |Six downloaded wheels matched exact declared SHA256 values and PyPI metadata|
| Offline source build with declared backend |Fresh no-cache build completed with no index and build isolation disabled|
| Formula syntax |`ruby -c packaging/homebrew/pratfall.rb.tmpl`: Syntax OK|

Artifact SHA256 values:

- Wheel: `777ebddeb01393a189eff2d7d7852c45a8ffdbbdaee904bc79d30d6946f76a23`
- Sdist: `f7347510c0399a85ea25d0f16a89061ddf4f297c8bfda190921a7f739ee9bfad`
- QA script: `af26c3ffaa42f9efca6a40c9fe2fe088196b1b777a5a6fe026a4ba8d6c346f1a`

The two installed packages, two archive manifests and current source each contain
the same 22 runtime Python files. The installed entry points resolve outside `src/`.
The result records all timeout/interruption/output-limit pending observations and
successful descendant-lock releases before the harness's safety cleanup.

The consumer run used the procedure in [the QA guide](../README.md), with
`--expected-version 0.1.0`, `--base-revision 0323ef6`, and unique root
`.cache/root-handoff-z2whmblo`. The final independent re-review is clean.
The last correction rejects prerelease metadata during publication retries and
adds five cases while removing two tests that only matched implementation strings;
this accounts for the suite increase from 419 to 422 without a runtime change.

The [first final-review run](2026-09-09-final-review-retest.json) remains historical
evidence for its earlier source archive. Its wheel bytes, 22 runtime files and
build inputs match the final artifacts. The exact backend wheels were installed
in `.cache/root-final-97f1_gtq/env-formula`. The cold-build check on that earlier
source archive was:

```sh
uv pip install --no-cache --reinstall --offline --no-index --no-deps \
  --no-build-isolation \
  --python .cache/root-final-97f1_gtq/env-formula/bin/python \
  .cache/root-final-97f1_gtq/artifacts/pratfall-0.1.0.tar.gz
```

The output explicitly reported building the source distribution. An earlier
offline installation reused uv's wheel cache, so it was not counted as independent
cold-build evidence. The declared backend environment passed dependency checking,
reported `prat 0.1.0` through the installed command, and listed all ten agents.

GitHub release publication and a real Homebrew installation were not performed.
Native git-cliff generation remains blocked by the running sandbox's read-only uv
tool directory. Both first-release template guards were checked against upstream
[issue 1620](https://github.com/orhun/git-cliff/issues/1620) and
[fix 1621](https://github.com/orhun/git-cliff/pull/1621).
