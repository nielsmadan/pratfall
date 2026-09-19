# Changelog

All notable user-facing changes to Pratfall. While the project is on `0.x`, breaking changes may
land in a minor release and are called out explicitly.

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

[0.9.1]: https://github.com/nielsmadan/pratfall/compare/v0.9.0..v0.9.1
[0.9.0]: https://github.com/nielsmadan/pratfall/tree/v0.9.0

