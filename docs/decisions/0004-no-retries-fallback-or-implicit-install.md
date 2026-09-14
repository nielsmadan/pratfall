# 0004 — No retries, fallback, or implicit install and login

**Status:** accepted

**Recorded:** 2026-09-14

## Context

A run is not safely repeatable. The agent edits the user's working directory, and the
[README](../../README.md) states that a timed-out or failed run can leave edits behind and that a
later manual rerun starts a fresh invocation. A silent retry, or a silent switch to another agent or
model, would therefore perform a second unrequested action against those files and spend the user's
credits doing it. These constraints are the most likely to be violated by someone adding value.

## Decision

Run one agent, once, for one prompt. No retry, no agent or model fallback, no implicit login or
install, no shell interpolation, and no writes to native configuration. Native authentication and
permissions are inherited by the child process, so Pratfall never handles a credential; `doctor`
checks availability without checking credentials, and `just install` and its siblings stay explicit
user actions. Prompts travel as argv or stdin bytes and never through a shell. Report every failure
through the `Code` vocabulary in [codes.py](../../src/pratfall/codes.py), whose fixed `EXIT_CODES`
table maps a code to an exit status, so a caller's own script owns any retry policy.

## Consequences

- A failed run leaves exactly the effects of the invocation the user asked for, and no rollback, because Pratfall cannot know which edits were the agent's.
- Pratfall stores no credential and cannot repair an unauthenticated agent; that failure surfaces instead of being papered over by a login prompt.
- The exit contract has to be stable and complete, since the caller is the retry loop; `codes.py` owns the fixed part while interruption, native signal and native exit stay computed in `normalize`.
- Transient provider failures are the user's to rerun by hand, from whatever state the failed run left — the cost the constraint deliberately accepts.
- The rule binds Pratfall, not the native agent: OpenClaw's own `--fallback` remains available as a native argument, and Pratfall still does not retry.
