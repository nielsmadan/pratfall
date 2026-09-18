# Running prompts

Run one prompt using an [agent name or alias](agents.md), or a [profile you've configured](profiles.md):

```sh
prat cx "review this change"
```

- [Supply a prompt](#supply-a-prompt)
- [Apply a template](#apply-a-template)
- [Include context files](#include-context-files)
- [Edit the prompt](#edit-the-prompt)
- [Extract code](#extract-code)
- [Set options and limits](#set-options-and-limits)
- [Pass native arguments](#pass-native-arguments)
- [Preview a run](#preview-a-run)
- [Watch progress and diagnostics](#watch-progress-and-diagnostics)
- [Handle timeouts and failures](#handle-timeouts-and-failures)
- [Output limits](#output-limits)
- [Include extra directories](#include-extra-directories)
- [Append instructions](#append-instructions)
- [Select a native agent](#select-a-native-agent)

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
With `--edit`, the initial draft may be empty or whitespace-only; all other input checks still apply.
Otherwise, an explicit prompt must be valid on its own; the combined prompt, including the separator, must
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

## Edit the prompt

```sh
prat cx --edit
git diff | prat cc -t review --context notes.md --edit
VISUAL='code --wait' prat cx -e "Review this draft"
```

`-e` or `--edit` opens the complete draft after stdin acquisition, template expansion and context
composition. You can start with no input or a blank draft, including an explicitly empty file or
an input-only template. The initial draft still must be UTF-8, NUL-free and within 1 MiB. After the
editor exits successfully, the saved file must also contain non-whitespace text before an agent
can run. Saving by replacing the file is supported.

Prat selects the first nonblank `VISUAL`, then `EDITOR`, then `vi`. The command is split using
shell-style quoting and run as arguments without a shell; variables, pipelines and command
substitutions are not expanded. Relative editor paths and the editor's working directory use the
invocation directory, independently of `--cwd`. Configure a GUI editor's wait flag when needed.
The draft lives in a private temporary Markdown file, removed after success or failure.

The editor reads and writes `/dev/tty`, so redirected stdin remains prompt input and stdout
remains the final answer or JSON result. A controlling terminal is required. Prat gives the editor
foreground terminal ownership and restores ownership and terminal settings afterward. Editor
failures return a normalized error before agent execution; SIGINT and SIGTERM return `128 + signal`
and terminate the editor's process group, escalating to SIGKILL after a bounded grace period.
Repeated signals accelerate cleanup. Editor descendants in that group are cleaned up on completion.

Editing time is outside `--timeout`. `--edit` and `--dry-run` are incompatible and fail before any
prompt or context input is read. The flag can appear before or after the selector, before `--`.

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
With a template or `--edit` and no explicit source, it counts as absent input. SIGINT or SIGTERM during input
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

## Include extra directories

```sh
prat cx --add-dir ../shared --add-dir "../other project" "review the integration"
```

`--add-dir PATH` is repeatable before or after the selector. It replaces profile/default
`add_dirs` with the supplied list, preserving order and duplicates. Relative CLI paths use the
invocation directory, independently of `--cwd`; config paths use their defining file's directory.
`~` expands to the user's home. Symlinks and `..` retain filesystem traversal semantics.
Spaces and leading dashes in names are supported; `--add-dir=-directory` is also accepted.

The selected paths must exist as directories, including for `--dry-run`. Unsupported settings,
native flag collisions and invalid directories fail before prompt files, stdin, context or editor
input is acquired. Directory contents are read by the native agent; paths are not snapshots.
`config validate` and `profiles` do not inspect those directories.

Claude, Codex and Copilot use native `--add-dir`; Gemini and Qwen use `--include-directories`.
Gemini and Qwen cannot represent commas or trailing whitespace in these paths, so Prat rejects
those names. See [agent directory semantics](agents.md#extra-directories) for access scope.
Native directory flags after `--` remain supported when public `add_dirs` is absent or empty;
combining them with a nonempty public list is an error, including Qwen's `--add-dir` alias.

Selected directory paths are canonicalized through the filesystem before native argv is built.
This preserves symlink/`..` targets even when a native parser would otherwise collapse `..`
lexically. Config inspection retains the joined source spelling and does not inspect targets.

## Append instructions

```sh
prat cc --instructions "Cite file paths in the answer" "review this change"
prat cx --instructions-file rules.md "review this change"
```

Claude, Codex, Qwen and Droid accept appended instructions. Choose exactly one form per invocation
or config table. The text is native instruction content, separate from the task prompt, templates
and context files; instructions alone do not supply a task. Prat preserves built-in guidance.
Codex sets `developer_instructions` for this run, replacing a value from native configuration
rather than concatenating with it. Native settings files are never changed.

Text and file content must be nonempty UTF-8 without NUL bytes and at most **1 MiB (1,048,576
bytes)**, independently of the prompt's 1 MiB limit. Whitespace-only content is rejected. Prat
preserves newlines and all accepted text exactly. It reads the winning file once, with a bounded
read from a regular file, before acquiring prompt stdin or starting an editor. Missing,
unreadable, nonregular, oversized and invalid UTF-8 files fail with the setting's source.
Unsupported settings and conflicting native arguments fail before reading the instruction file.

CLI file paths resolve from the invocation directory, independently of `--cwd`; config file
paths resolve from the defining config's directory. `~` expands, and symlink/`..` traversal keeps
filesystem semantics. Shell syntax and variables in instruction text are literal data.
Only the selected winning file is read; see [override rules](profiles.md#appended-instructions).
`--dry-run` reads and validates it and displays the native argument containing its contents.

All four adapters pass the prepared content in native argv. Operating-system argument-size
limits can be lower than Prat's input limit, including when using `--instructions-file`.
The append flags in existing Claude/Qwen `native_args` remain available when neither public
instruction form is active; combining them with public instructions is an error.

## Control tool availability

```sh
prat cc --tools Read --tools Grep --disable-tools 'mcp__*' "review this checkout"
prat qwen --tools ReadFileTool --disable-tools ShellTool "summarize the README"
prat vibe --tools 'read*' --disable-tools 're:^bash$' "review this checkout"
```

Repeat `--tools NAME` and `--disable-tools NAME` before or after the selector. Each occurrence is
one native name or pattern; Prat preserves order and duplicates. CLI lists replace configured
lists. Use `--tools=-name` when a value starts with a dash. Config `tools=[]` is an active empty
allowlist; an empty CLI string is invalid. The names, patterns and effective tool set belong to
the native agent, and an unknown name can fail or match nothing according to that agent.

| Agent | Allowlist (`--tools`) | Exclusions (`--disable-tools`) | Empty allowlist |
| --- | --- | --- | --- |
| Claude | `--tools`; built-in names such as `Read`, `Grep`, or native preset `default` | `--disallowedTools`; names, MCP patterns and permission rules such as `Bash(rm *)` | `--tools=""`; built-ins only, with the exception below |
| Qwen | `--core-tools`; native names and aliases such as `ReadFileTool` | `--exclude-tools`; native deny rules | Rejected |
| Copilot | `--available-tools`; model-visible native tool names | `--excluded-tools`; remove native tools from model visibility | Bare `--available-tools` |
| Droid | `--restrict-tools`; native tool IDs such as `ApplyPatch` | `--disabled-tools`; native tool IDs such as `execute-cli` | Rejected |
| Vibe | Repeated `--enabled-tools`; exact names, globs, or `re:` regular expressions | Repeated `--disabled-tools`; same pattern syntax, applied after the allowlist | Rejected |

Claude's allowlist does not remove MCP tools. `EndConversation` can remain despite allowlists and
deny rules while another tool is available. A scoped deny rule keeps the tool visible and denies
matching calls; it is not equivalent to removing the entire tool. See the
[Claude contract](../reference/claude.md#tool-availability-verified-2026-09-18).

Qwen's v0.24 tool contract filters ordinary native tools but its synthetic schema tool bypasses
core allowlists and obeys explicit excludes and permission denials. Native configuration still
participates: Qwen combines configured core entries with CLI entries when deciding exemptions
from automatic headless exclusions. Selecting a write/execute tool can remove that default
headless exclusion, without adding a permission allow rule. Prat never inserts `--allowed-tools`
or removes explicit native denials. See the [Qwen contract](../reference/qwen.md#tool-availability-verified-2026-09-18).

Vibe filters native available tools, including MCP and connector tools, with exclusions applied
last. Native source disabling and permission decisions remain authoritative. These controls
select availability; Prat adds no permission approval, retries, or OS sandbox.

Commas are rejected in values for Claude, Qwen, Copilot and Droid because those native list
transports split commas. Surrounding native whitespace is rejected too. Claude allowlist names
and Droid IDs also reject Unicode whitespace, including U+FEFF, because their tool-name lists
have space-delimited forms. Claude/Qwen exclusion patterns retain internal spaces. Vibe patterns
retain commas, spaces and regular-expression syntax. Quote patterns to protect them from your shell.

Accepted native-only tool arguments remain supported after `--`. A corresponding active public
setting rejects equivalent native flags, including Claude's `--disallowed-tools` alias. A public
Copilot allowlist also conflicts with `--enable-all-github-mcp-tools`, `--enable-mcp-server`,
`--add-github-mcp-tool`, and
`--add-github-mcp-toolset`; Droid tool controls conflict with `--additional-tools`.
Use either the public setting or its native counterpart. Native permission denials can still be
combined with public availability controls.

## Select a native agent

```sh
prat cc --native-agent reviewer "review this change"
prat cp --native-agent code-review "review the latest commit"
prat vibe --native-agent plan "plan this change"
```

`--native-agent NAME` selects an existing native agent/persona for Claude, Copilot or Vibe.
The Prat selector still chooses the backend or a Prat profile; this setting becomes native
`--agent=NAME`. Prat does not discover names, create agent definitions, or validate whether a
name exists. Native resolution and errors remain authoritative. Names must be nonblank, NUL-free
UTF-8; spaces, punctuation and case are preserved. Use `--native-agent=-leading-dash` for a
name beginning with a dash. Omission preserves native agent selection.

Selecting a persona can change native instructions, models, tools and permissions. In particular,
Vibe's `auto-approve` agent can automatically approve tool calls. Prat adds no approval flags
and does not restrict the permission behavior of the selected native agent.

An active public selection conflicts with native `--agent` in `native_args` or after `--`.
Previously supported native-only selection remains accepted for Claude, Copilot and OpenCode;
Vibe's native `--agent` remains reserved, so use the public flag. OpenCode has no public support
because its CLI falls back to the default agent for unknown or subagent-only names.
Unsupported public selection and native collisions fail before prompt input or editor startup.

## Native attachments

```sh
prat cx --attach screenshot.png --attach detail.png "compare these screenshots"
prat hm --attach photo.jpg "describe this image"
prat cp --attach report.pdf "summarize this document"
prat oc --attach source.py "review this file"
```

`--attach PATH` is repeatable before or after the selector and replaces inherited `attachments`.
Order and duplicates are preserved. CLI paths use the invocation directory, independently of
`--cwd`; config paths use their defining file's directory. Home expansion is supported. Spaces,
Unicode and leading dashes are literal (`--attach=-image.png`); Codex rejects commas because its
native image flag splits them. Restrictions also apply to the canonical target of a symlink.
Hermes accepts one image; multiple attachments fail before prompt acquisition.

Attachments supplement a task supplied through normal prompt input, a template or the editor.
They do not supply a task themselves, prepend text or consume stdin. Redirected stdin still follows
the usual prompt composition rules. `--attach -` names a local file called `-`.

The selected files must be local, regular and readable. Prat resolves symlinks through the filesystem,
opens each nonblocking, checks its descriptor type, and reads at most one byte to check readability.
Binary data is not decoded and Prat imposes no attachment payload-size cap. Native agents read the
original canonical paths later: files may change between validation and use. Native format,
image-capable model and payload limits remain authoritative; a recognized filename extension does
not establish valid content. `--dry-run` performs these same local checks without launching an agent.

Native equivalents after `--` remain accepted when the public attachment array is absent or empty.
An active public list rejects equivalent native options, including Codex `-i` and OpenCode `-f`.
