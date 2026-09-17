# Running prompts

Run one prompt using an [agent name or alias](agents.md), or a [profile you've configured](profiles.md):

```sh
prat cx "review this change"
```

- [Supply a prompt](#supply-a-prompt)
- [Apply a template](#apply-a-template)
- [Include context files](#include-context-files)
- [Extract code](#extract-code)
- [Set options and limits](#set-options-and-limits)
- [Pass native arguments](#pass-native-arguments)
- [Preview a run](#preview-a-run)
- [Watch progress and diagnostics](#watch-progress-and-diagnostics)
- [Handle timeouts and failures](#handle-timeouts-and-failures)
- [Output limits](#output-limits)

## Supply a prompt

Choose one explicit prompt source:

| Source | Example |
| --- | --- |
| Inline text | `prat cx "review this change"` |
| File | `prat cx --file request.md` |
| Explicit stdin | `cat request.md \| prat cc -` |
| Redirected stdin | `cat request.md \| prat cc` |
| Text starting with a dash | `prat cc --prompt=-leading-dash` |

`-f PATH` and `--file=PATH` also read files; `--file -` reads stdin. `--prompt=-` passes a literal
dash. Without an explicit source, Prat reads redirected stdin; at a terminal, it reports a missing
prompt.

Redirected stdin is also read when you supply inline text or `--file PATH`. Nonempty stdin is
placed first, followed by two newline bytes and the explicit prompt. Both inputs retain their
original whitespace. Empty stdin, a terminal, or closed stdin leaves the explicit prompt unchanged.
An open stdin stream that cannot be read is an input error.

```sh
prat cx "how many Rs in strawberry" | prat cx "times 5"
git diff | prat cc "Review these changes"
```

Use `< /dev/null` when an explicit prompt should run without inherited stdin. The `-` and
`--file -` forms read stdin once, without adding a separator or another copy of the input.

Prompts must be valid UTF-8, contain non-whitespace text, have no NUL bytes, and fit within 1 MiB.
An explicit prompt must be valid on its own; the combined prompt, including the separator, must
also fit within that limit.
Files must be regular files or symlinks to regular files. Invalid input fails with exit 2 before
an agent starts. Relative file paths resolve from the invocation directory, independently of
`--cwd`.

Gemini, Copilot, Cursor, Kiro, OpenHands, Warp, iFlow, Devin, and Grok receive prompts as command
arguments, so the operating system's argument-size limit may be lower than 1 MiB.
Reasonix, Kimi, Vibe, and Grok trim surrounding whitespace natively. Crush 0.93.1 adds two trailing
newlines to the original input. Prat supplies the same prompt bytes before these native changes.

## Apply a template

Define reusable prompts in the same global or local version-1 TOML configuration:

```toml
[templates.review]
prompt = "Review for bugs:\n\n$input"

[templates.summary]
prompt = "Summarize the current project."
```

```sh
git diff | prat cc -t review
prat --template=review cc --file request.md
prat cc --template summary
prat templates
```

Use `-t NAME`, `--template NAME`, or `--template=NAME` once, before or after the selector.
Everything after `--` still belongs to the native agent. Unknown template names fail before stdin
is read. `prat templates` lists names in sorted order with JSON-quoted prompt strings.

`$input` and `${input}` substitute the base prompt, including the existing stdin-first combination
when both redirected stdin and an explicit source are supplied. `$$` produces one literal dollar
sign. Other dollar expressions are invalid; use `$$` wherever a literal dollar is needed.
Substitution happens once: dollar expressions in input text remain literal. Without an input
placeholder, a template appends nonempty base input after two newlines. Whitespace and line endings
are preserved.

A template can provide the complete task when no base input is supplied. Terminal, closed or
unavailable stdin and an empty implicit stdin stream count as absent input. Explicit empty sources
(`--prompt=`, an empty file, or explicit `-`/`--file -` stdin) still fail; whitespace-only implicit
stdin also fails. The final rendered task must be nonempty, valid UTF-8, NUL-free and at most 1 MiB.
Template definitions also have a 1 MiB UTF-8 limit, and repeated substitutions are checked against
the final limit before allocating their expanded text. `--dry-run` uses the same rendered prompt.

## Include context files

```sh
prat cc "Review this change" --context notes.md --context design.md
prat --context=notes.md cc -t review --file request.md
```

Repeat `--context PATH` or `--context=PATH` before or after the selector. Files are included in
argument order, including duplicates. Paths resolve from the invocation directory, independently
of `--cwd`; `--context -` reads a file named `-`. Files must be local regular files (symlinks to
regular files are accepted), valid UTF-8 and NUL-free. Empty and whitespace-only context files
are allowed. Content bytes, including original line endings, are preserved.

Each file adds `# Context: <JSON-quoted supplied path>\n\n<file bytes>\n\n` before the task.
Labels use JSON escaping, including escapes for non-ASCII characters and newlines. They are
presentation labels, not a security boundary. Context is added after template expansion and
remains outside substitution. A context file does not supply a missing task.

The shared 1 MiB limit includes the rendered task, all context content, labels and separators.
Prat reserves label space before reading context files, reads only up to the remaining budget
plus one overflow byte, and stops on overflow before opening later files. `--dry-run` uses the
same composition and validation.

## Extract code

```sh
prat cc -x "Write a Python function"
prat --extract cx "Write a shell script" --json
```

`-x` or `--extract` returns the body of the first fenced code block in the decoded answer.
The opening line must have zero to three leading spaces followed by at least three backticks
or tildes. An optional info string is allowed; backtick fences cannot have backticks in that
string. A closing line has zero to three leading spaces, the same fence character repeated at
least as many times, and only spaces or tabs afterward. Lines end at LF, CRLF or CR.

The first opening fence governs: if it is never closed, or no opening exists, the entire answer
is returned unchanged. A complete empty block produces empty output. Body indentation, whitespace
and line endings are preserved without dedenting. Ordinary text output keeps its existing behavior:
nonempty output gets a final LF if it lacks one, while empty output writes nothing. JSON `output`
contains the exact extracted body.

Extraction applies to successful and failed runs, including partial answers. It changes only
`output`; errors, status, accounting, exit codes and native `--trace` remain unchanged. It has no
effect on a `--dry-run` preview. Like other run flags, it belongs before the native `--` boundary
and can appear before or after the selector.

## Set options and limits

Run options can appear before or after the selector and override supported profile fields:

```sh
prat cx --model gpt-5.6-luna --effort low "review this change"
prat cc --timeout 120 --cwd ../project "inspect this failure"
```

| Control | Flags and behavior |
| --- | --- |
| Model and effort | `--model`, `--effort`; support depends on the agent. |
| Fast mode | `--fast` or `--no-fast` for Claude and Codex. Omitting both preserves native settings; combining them fails before launch. |
| Execution deadline | `--timeout SECONDS`; defaults to 600 seconds. |
| Native budgets | `--max-budget-usd`, `--max-turns`, `--max-ai-credits`; support and counting depend on the agent. |
| Working directory | `--cwd PATH`; changes the agent's directory, not prompt-file resolution or config discovery. |
| Configuration | `--config PATH`; selects a local file to merge over global config. |

See [agent controls and limitations](agents.md#shared-controls-and-limitations) for budget
semantics and [profiles](profiles.md) for configuration merging. Native limits retain the agent's
counting and overshoot behavior. Hermes `--run-budget` controls native wrap-up time;
Prat's `--timeout` remains the outer deadline.

## Pass native arguments

Put supported native options after `--`:

```sh
prat cx "inspect this change" -- --sandbox read-only --ephemeral
prat cc "make the requested edit" -- --permission-mode acceptEdits
```

These arguments replace the profile's `native_args`. Prat accepts a documented set of native
options and rejects positional arguments and controls that conflict with its prompt, output,
session, working directory, model, effort, fast mode, or budget settings. See
[agent-specific options](agents.md#native-behavior). Other options can be supplied through a
trusted [command wrapper](profiles.md#configure-commands-and-wrappers).

Prat preserves native permission defaults and inherits authentication and settings. It does not
install agents, log in, or edit native configuration.

## Preview a run

```sh
prat cx "review this change" --dry-run
```

`--dry-run` reads and validates the prompt, then prints the resolved command without launching it.
Prompts sent through stdin appear as a byte count; prompts sent as command arguments appear in
full. Add `--json` for a machine-readable preview.

## Watch progress and diagnostics

Final output goes to stdout; progress and diagnostics go to stderr. With `--json`, stdout contains
one [result object](json.md).

| Flag | What appears on stderr |
| --- | --- |
| `--progress` | Elapsed time and activity categories such as reasoning, tool use, and answering. |
| `--trace` | Captured native stdout, including any native payload text. |

Progress updates contain category labels only, with at most one update per second and a heartbeat
at least every five seconds. `--progress` applies to one run and cannot be saved in a profile.
A slow stderr reader can cause progress, trace, and diagnostic writes to be dropped in this mode,
so they do not delay the agent deadline. Trace can show only what the native CLI emits.

## Handle timeouts and failures

Prat validates configuration, template names, native options, and `--cwd` before reading input.
It waits for EOF before starting the execution timeout. Closed or unavailable stdin is an input
error when stdin is explicitly requested or no other prompt source or template is supplied.
With a template and no explicit source, it counts as absent input. SIGINT or SIGTERM during input
acquisition exits with `128 + signal` without launching an agent.

A failed or timed-out run can leave edits in the working directory. Prat does not retry, roll back
edits, or fall back to another agent or model. A manual rerun starts a fresh invocation.

Failed results can retain partial output and accounting. Diagnostic write failures trigger
process-group cleanup while preserving final output and accounting. JSON reports `output_io_error`
unless a timeout, interruption, or runner error takes precedence. Unexpected failures report
`internal_error`; after a result has reached stdout, the failure is reported only on stderr. See
[JSON errors and partial results](json.md#errors-and-partial-results) for the full contract.

## Output limits

Streaming output from Codex, Copilot, Antigravity, OpenCode, Warp, Qwen, Amp, Kimi, and OpenHands is
decoded as it arrives. Other JSON and text adapters retain complete stdout.

| Output | Limit |
| --- | --- |
| Individual streaming event and retained answer/protocol state | 8 MiB each |
| Retained logical records | 16,384 |
| Complete stdout for non-streaming adapters, or trace capture | 8 MiB |
| Native stderr | 2 MiB |

Without `--trace`, discarded streaming events have no cumulative output limit, so a long run with
many small events can exceed 8 MiB in total. Crossing a limit fails the run and cleans up the owned
process group.
