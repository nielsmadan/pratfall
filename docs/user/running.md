# Running prompts

Run exactly one prompt through a built-in selector or named profile:

```sh
prat cx "review this change"
prat simple --effort low "rebase these commits"
printf 'multiline\nprompt\n' | prat cc -
printf 'redirected prompt\n' | prat cc
prat cx --file request.md
prat cc --prompt=-leading-dash
```

Supply exactly one explicit prompt source: positional text, `--prompt=TEXT`, `-f PATH` /
`--file PATH`, or stdin through positional `-` or `--file -`. Use `--prompt=TEXT` when prompt text
begins with a dash; `--prompt=-` is a literal dash. `--file=PATH` is also accepted. With a selector
and no explicit source, Prat reads redirected stdin. It does not prompt at a terminal. An explicit
source leaves incidental redirected stdin unread.

Every source must be valid UTF-8, nonempty and not whitespace-only, contain no NUL bytes, and stay
within 1 MiB. Named files must be regular files; symlinks to regular files work, while missing,
unreadable, directory, and special-file inputs fail with exit 2 before an agent starts. Relative
file paths resolve from the directory where Prat was invoked, independently of `--cwd`. Gemini,
Copilot, Cursor, and Kiro carry prompts in argv and can hit an operating-system argv limit below
1 MiB.

Run options can appear before or after the selector. `--model`, `--effort`, `--fast`, `--no-fast`,
`--timeout`,
`--max-budget-usd`, `--max-turns`, and `--max-ai-credits` override supported profile fields.
Fast overrides are supported by Claude and Codex. Omitting both flags preserves native behavior;
`--no-fast` is a real false override. Conflicting fast flags fail before launch.
`--cwd PATH` changes the child working directory. `--dry-run` acquires and validates the selected
input, then prints the resolved argv without launching the agent. Prompts carried through stdin
appear only as a byte count. Gemini, Copilot, Cursor, and Kiro carry the prompt in argv, so their
previews include it.

Use `--progress` to print bounded elapsed-time and activity updates on stderr while an agent runs.
Structured streaming adapters report only static categories such as reasoning, tool use, and
answering; Prat never includes native event payloads, prompts, commands, reasoning, or answer text
in progress lines. Updates are coalesced to at most one per second, with a heartbeat at least every
five seconds. Backpressure on stderr drops updates rather than delaying the agent deadline. The
flag applies to one run and is not a profile setting. It does not change final stdout: JSON mode
still writes exactly one result object.

Input acquisition waits for EOF before the agent-execution timeout begins. Unavailable or closed
standard input is an input error; provide inline text or a regular file instead. SIGINT or SIGTERM while
Prat is reading input returns interrupted status and exits with `128 + signal` without launching an
agent. A timed-out or failed run can leave edits in the working directory. A later manual rerun is
a fresh invocation; Prat does not retry automatically or roll back native edits.
Those results can also contain partial final output and any accounting decoded before failure.
In particular, Codex assistant messages completed before EOF or the outer timeout are preserved,
but an unfinished turn still fails rather than becoming a synthetic success.

Codex, Copilot, Antigravity, and OpenCode JSONL output is decoded as it arrives. Prat limits each
physical event and retained answer/protocol state to 8 MiB and retains at most 16,384 logical
records. Discarded events do not count toward a cumulative trace limit, so long runs with many
small progress events can exceed 8 MiB in total. Other structured JSON and text adapters retain
their complete stdout and keep the 8 MiB whole-output limit. Native stderr is limited to 2 MiB.
Crossing a limit fails the run explicitly and cleans up the owned process group.

Arguments after `--` are trusted native argv and replace configured `native_args`. Pratfall accepts
only a finite documented option set and rejects native prompt, output, session, cwd, model, effort,
fast, and budget controls that it owns. Unsupported options can be placed in a trusted executable
wrapper.

Pratfall preserves native permission defaults. It does not retry, fall back to another agent or
model, install or log into commands, or edit configuration. Hermes `--run-budget` is a native
wall-clock wrap-up budget; Pratfall's `--timeout` remains the outer process deadline.
