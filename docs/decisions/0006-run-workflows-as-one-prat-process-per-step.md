# 0006 — Run workflows as one prat process per step

**Status:** accepted

**Recorded:** 2026-10-13

## Context

A workflow runs several profiles in sequence or in parallel and passes outputs between them. The
runner owns the lifecycle of exactly one native process group, and the run path reads configuration,
binds prompt parameters, composes the prompt, applies the profile and normalizes one result. A
second orchestration of the same steps inside one process would duplicate that path, and it would
share signal handlers, standard streams and cleanup state between concurrent runs. The result
shape a caller already understands is the single-run `--json` object.

## Decision

The workflow parent never runs an agent. For each step it starts one child, `python -P -m
pratfall __step`, in its own session, with the step's arguments: the profile selector, `--json`,
`--no-notify`, the passed-through `--config`, `--cwd`, `--timeout`, `--progress` and `--trace`,
and one `--param` per prompt parameter. The step spec (`version`, the parent pid, the arguments,
and the input text or null) goes to the child as JSON on stdin, from an unlinked temporary file, so
upstream output never travels through argv or a shell. `-P` keeps a project directory from shadowing
the child's imports. The child detaches its own stdin, runs the ordinary run path, and prints its
normal JSON result; the parent validates that result, feeds its `output` downstream, and assembles
the workflow result from the step results.

The parent installs handlers for SIGINT, SIGTERM and SIGHUP and forwards each to every running
child's process group, SIGHUP as SIGTERM; a SIGHUP inherited as ignored stays ignored, so `nohup`
keeps working. Teardown signals the children directly with TERM, a grace
period, TERM again and then KILL. Each child also watches the parent pid in the spec and terminates
itself if the parent disappears, so a killed parent does not leave steps running. Result size,
relayed stderr lines and the spec are bounded.

The parent resolves parameters, timeouts, flags and the working directory before launching
anything, and builds a synthesized step result for failures it cannot take from a child.

## Consequences

- A step is exactly a normal run, so its result, error codes and exit status keep their meaning, and
  step processes are isolated from one another.
- Each step loads configuration when it starts, so edits during a workflow apply to later steps.
- Interrupting the parent interrupts every step; a workflow is only as safe against an unclean
  parent exit as the child watchdog's polling interval allows.
- ADR 0004's "one agent, once" now holds per step. A failed step is reported and its dependents are
  skipped; there is still no retry and no agent or model fallback, and a caller's script owns any
  retry policy.
- Parallel steps share one working directory, and Pratfall does not detect conflicting edits.
- A step with `each` runs one child per item through the same entry, so an instance is a normal
  run and a long list is bounded by a per-step `max_parallel` queue.
- Step output relayed to stdout is limited to the final steps' combined output; per-step detail is
  in the `--json` `steps` array.
