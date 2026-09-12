# Architecture

`prat` follows a one-way flow:

1. `cli.py` separates management and run syntax, loads configuration, and resolves one prompt.
2. `config.py` validates all profiles and applies invocation, profile, and default precedence.
3. `catalog.py` supplies immutable agent metadata and capability checks.
4. The selected module in `adapters/` builds an argv/stdin invocation.
5. `runner.py` starts a process group, drains bounded stdout and stderr concurrently, feeds an
   optional neutral byte consumer, and enforces one monotonic deadline.
6. The adapter consumer finalizes native output, usage, and verified native accounting;
   `output.py` applies exit/error precedence to one normalized result while retaining decoded
   fields.

The adapter registry binds command builders, convenience decoders, and optional incremental
consumer factories. Codex, Copilot, Antigravity, OpenCode, Warp, Qwen, Amp and Kimi share a strict
byte-oriented JSONL framer. OpenHands has a separate bounded consumer for its mixed SDK events
and native prose. Each adapter owns its protocol transitions. The runner does not know provider
schemas, and adapters do not own process lifecycle. It feeds cleanup bytes before finalizing once;
after a hard local output failure it drains stdout without reparsing it. The standard-library-only
runtime keeps source and Homebrew installation free of vendored Python resources.
Encoding or presentation failures detected after the parent exits still trigger bounded cleanup of
the owned process group, because descendants can remain after the native parent closes its pipes.
An existence probe (`killpg(group, 0)`) returning EPERM keeps the group pending within those
existing deadlines. Apple's [XNU group-signal implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_sig.c#L1709)
excludes zombie members and can return EPERM while the last members exit. A source-entry-point
regression run observed SIGTERM succeed followed by EPERM from the next zero-signal probe. Cleanup waits for a later
disappearance observation; persistent probe denial still fails cleanup, and permission failures
from SIGTERM or SIGKILL remain explicit errors.

stdout is reserved for final text or one JSON object. Native stderr and Pratfall progress go to
stderr. Opt-in progress uses static activity categories through a bounded nonblocking sink, which
also presents launch, completion, and native diagnostics in that mode. The sink restores stderr's
descriptor flags. Child stdin is always explicit bytes or closed; it never inherits the user's
terminal.

Incremental JSONL consumers bound each physical event and live retained state to 8 MiB, retain at
most 16,384 logical records, and bound numeric representations. Replaced snapshots refund their
prior state. Discarded events have no cumulative byte cap. Whole-document JSON and text adapters
keep the 8 MiB complete stdout limit, and native stderr keeps its 2 MiB limit.

Version diagnostics reuse the same runner with explicit 64 KiB stream bounds and a three-second
deadline. `cli.py` selects opaque version text and records per-agent probe errors; the runner keeps
the same process-group cleanup and interruption behavior as ordinary runs.
