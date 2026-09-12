# Documentation

The [README](../README.md) is the main user manual. These Markdown references follow the Lean
profile: a few builder guides, plus separate user guides, external references, and decisions.

| I need to… | Start here |
| --- | --- |
| Install Pratfall, configure a profile, or run a prompt | [User guides](user/overview.md) |
| Change execution, decoding, or process cleanup | [Execution and adapter boundaries](execution.md) |
| Check a native agent's protocol, version baseline, or source evidence | [Native agent interfaces](reference/overview.md) |
| Prepare a release or maintain Homebrew distribution | [Release and distribution](release.md) |
| Repeat installed-package checks | [Local package verification](release.md#check-installed-packages-locally) |
| Understand the documentation choice | [README and Markdown guides](decisions/0001-use-readme-and-markdown-guides.md) |

## Maintenance

- User and builder guides describe current behavior and follow code changes.
- `reference/` tracks external interfaces. Preserve evidence dates and versions; update them only
  after rechecking the corresponding source or probe.
- `decisions/` records accepted choices. Supersede an ADR with a new record rather than rewriting it.
- Keep repeatable verification commands in the relevant guide and results with release artifacts.
- Harvest useful knowledge from completed plans, specs, reviews, and QA output, then remove them.
