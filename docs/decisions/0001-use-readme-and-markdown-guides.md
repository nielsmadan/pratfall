# 0001 — Use a README with linked Markdown guides

**Status:** accepted

**Recorded:** 2026-09-12

## Context

Pratfall's everyday interface is small: select an agent or profile and supply a prompt. Detailed
knowledge lies in native-agent differences, execution constraints, and release operations. The
user chose to remove the documentation site and retain a README with linked Markdown references.

## Decision

Use the README as the main manual. Keep task-oriented guides in `docs/user/`, a small number of
builder guides directly under `docs/`, and native-interface evidence in `docs/reference/`.
Keep repeatable package checks in the release guide and their results with release artifacts.
Harvest durable knowledge from completed plans, specs, reviews, and QA output, then remove the
working documents. Maintain repository Markdown without a separate site build or deployment.

## Consequences

- Documentation changes travel with the implementation without site dependencies or publication steps.
- User instructions, implementation details, and version-specific external evidence have separate owners.
- Readers use repository search and links rather than site search or versioned documentation navigation.
- Builders must maintain Markdown links when moving files; successful code checks do not establish link validity.

See the current [documentation map](../overview.md) for entry points.
