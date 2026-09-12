# Initial A-tier installed verification

The documented offline build/install procedure completed both fresh installations against runtime
revision `98a615b59ed656a8f9dca1934341763b3b7e3832`. The CLI harness exited 1 at E08; the full
scenario matrix is incomplete and is not passing evidence. Runtime files were committed; the
Task 5 QA harness and public guides were still modified.

The A-tier fixture dispatcher matched `QA_ACCOUNT_CLAUDE` (and the other existing accounting
fixtures) with its broad `QA_A` prefix. It raised KeyError before the old fixtures could emit their
valid accounting envelopes. Installed Prat correctly surfaced the fake native exit 1. This is a
harness defect. Root reproduced it separately for Claude, Gemini, Copilot, OpenClaw and OpenCode;
all five failed with the same dispatch cause. No real native agent ran.

[Commands and complete exit output](2026-09-11-a-tier-initial.log) and
[artifact/script identities and five focused reproductions](2026-09-11-a-tier-initial.json)
preserve the original failure. The procedure is [installed consumer QA](../README.md).
A scoped harness repair and fresh complete offline run follow; this record remains historical.
