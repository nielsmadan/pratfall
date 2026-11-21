# Changelog

All notable user-facing changes to Pratfall. While the project is on `0.x`, breaking changes may
land in a minor release and are called out explicitly.

## [1.3.0] - 2026-11-21

### Features

- Refuse a separate flag value that starts with a dash
- Let the last of --fast/--no-fast and --notify/--no-notify win
- Accept --prompt TEXT like other value flags
- Read stdin beside an explicit prompt only through --context -
- Raise the prompt limit to 8 MiB for agents that read stdin
- Pass any native flag that prat does not control
- Remove --max-ai-credits in favour of Copilot's native flag
- Map fast mode for Amp
- Stop passing --session-id to Copilot, where a reused id resumes
- Raise the default timeout to an hour and let none or 0 turn it off
- Append arguments after -- to a profile's native arguments
- Remove --tools and --disabled-tools in favour of each agent's native tool flags
- Remove --native-agent in favour of each agent's native --agent flag
- Reserve only the native flags that would break a run

### Bug Fixes

- Name 1.2.0 as the release that removed OpenHands and iFlow
- Name the rejected value in run option number errors
- Say how to quote extra prompt words and name the source of empty input
- Drop the Reasonix --fail-on-unverified flag that 2.58.0 removed
- Word the --cwd directory error like the other path options
- Place prat's own prompt token before native arguments for Kimi, Codex and Cursor
- Show an off timeout as none in text and 0 in every JSON view

## [1.2.0] - 2026-11-08

### Features

- Add prompt parameters to profiles and templates
- Add workflows with parallel fan-out and merges
- Fan out workflow steps over output items with a queue
- Add max_items and overflow to workflow fan-out
- Refresh native agent integrations
- Support opencode 2
- Trust folders with prat trust and find .pratfile in parent directories
- Add prat trust --list and --revoke and reject --config
- Rename --attach and --disable-tools to match their config keys
- Make trust list and schema_transport JSON follow the conventions
- Show launch and finish lines only when stderr is a terminal
- Exit 1 when a native status collides with prat's reserved exit codes
- Name flags and rejected values in errors and suggest near matches
- Write every error message in one lowercase style without trailing periods
- Tighten near-match suggestions and name agents removed in 0.9.0
- Add selector-aware help and describe each management command
- Fail a run without a selector unless bare prat runs at a terminal
- Add prat help and prat version commands
- Replace the listing commands with prat list and show full selector help pages
- Print help pages as JSON with --json
- Drop only the profile or workflow a built-in name shadows

### Bug Fixes

- Keep opencode partial output when the text budget is exceeded
- Treat pi output token limit as a provider error
- Ignore qwen child results in plain mode
- Apply functional review findings
- Resolve test review findings
- Require trust for a local .pratfile and harden workflow output
- Fail at once when prat __step is typed at a terminal
- Print a selected shadowed name's collision once
- Match release QA to the 1.2.0 help and error text

## [1.1.0] - 2026-10-13

### Features

- Add bash and zsh shell completion

## [1.0.0] - 2026-10-11

### Features

- Request and report native session ids
- Profile file references and composition
- Add completion notifications
- Add command_prefix to wrap every agent command
- Add whodunit README demo

### Bug Fixes

- Sanitize streamed session ids

## [0.9.1] - 2026-09-19

### Features

- Add grok build adapter
- Add native output tracing
- Combine piped input with explicit prompts
- Add named prompt templates
- Add repeatable context files
- Extract fenced code from responses
- Compose prompts in an editor
- Add extra workspace directories
- Add appended agent instructions
- Add native tool availability controls
- Add native agent selection
- Add native file attachments
- Add structured schema output
- Support codex and qwen schemas
- Add pi support
- Accept codex native config overrides

### Bug Fixes

- Ship py.typed marker
- Normalize recursion and numeric overflow in the older decoders
- Apply one JSON strictness rule to every whole-document decoder
- Normalize unexpected failures instead of printing a traceback
- Report rejected native arguments as invalid_arguments
- Report command-line option rejections as invalid_arguments
- Bound max_turns to the native u32 range
- Stop pinning the dry-run preview against a saturated pipe
- Stabilize release test diagnostics

## [0.9.0] - 2026-09-13

### Features

- Add agent profiles
- Run Claude and Codex prompts
- Add five agent adapters
- Complete agent adapters
- Read prompts from files and pipes
- Add fast mode and version diagnostics
- Report native accounting and partial answers
- Stream agent output with live progress
- Expand supported coding agents
- Simplify agent selectors
- Merge local and global configuration

### Bug Fixes

- Harden pratfall
- Create pytest basetemp parent so a fresh checkout can run the suite (#1)
- Harden cli failure handling

[1.3.0]: https://github.com/nielsmadan/pratfall/compare/v1.2.0..v1.3.0
[1.2.0]: https://github.com/nielsmadan/pratfall/compare/v1.1.0..v1.2.0
[1.1.0]: https://github.com/nielsmadan/pratfall/compare/v1.0.0..v1.1.0
[1.0.0]: https://github.com/nielsmadan/pratfall/compare/v0.9.1..v1.0.0
[0.9.1]: https://github.com/nielsmadan/pratfall/compare/v0.9.0..v0.9.1
[0.9.0]: https://github.com/nielsmadan/pratfall/tree/v0.9.0

