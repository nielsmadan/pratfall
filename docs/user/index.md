# Pratfall

Pratfall runs one prompt through an installed coding-agent CLI. Short selectors and named profiles
make repeatable one-shot commands concise while preserving each agent's native authentication,
configuration, and permission defaults.

```sh
prat cx "summarize the changes in this checkout"
prat simple "review this code"
printf 'review these files\n' | prat cc -
```

Pratfall supports Claude Code, Codex, Gemini, Antigravity, Copilot, Kiro, Cursor, OpenClaw, Hermes,
and OpenCode. Start with [installation](getting-started.md), then define reusable
[profiles](profiles.md) or read the complete [run guide](running.md).
