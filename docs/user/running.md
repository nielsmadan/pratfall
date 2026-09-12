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

Input acquisition waits for EOF before the agent-execution timeout begins. SIGINT or SIGTERM while
Prat is reading input returns interrupted status and exits with `128 + signal` without launching an
agent. A timed-out or failed run can leave edits in the working directory.

Arguments after `--` are trusted native argv and replace configured `native_args`. Pratfall accepts
only a finite documented option set and rejects native prompt, output, session, cwd, model, effort,
fast, and budget controls that it owns. Unsupported options can be placed in a trusted executable
wrapper.

Pratfall preserves native permission defaults. It does not retry, fall back to another agent or
model, install or log into commands, or edit configuration. Hermes `--run-budget` is a native
wall-clock wrap-up budget; Pratfall's `--timeout` remains the outer process deadline.
