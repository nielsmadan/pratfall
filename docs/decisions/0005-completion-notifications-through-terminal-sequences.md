# 0005 — Completion notifications through terminal escape sequences

**Status:** accepted

**Recorded:** 2026-09-25

## Context

Agent runs take minutes, and users switch away while they run. There is no cross-platform desktop
notification API in the standard library. The OS commands have these problems:

- **macOS `osascript`:** fails silently with exit 0 unless Script Editor has notification
  permission.
- **`notify-send`:** needs a session D-Bus, and over SSH it can reach the remote host's desktop.
- **Windows toasts:** need PowerShell 5.1 with a borrowed AppUserModelID, or a registry write.

Terminal emulators accept notification escape sequences instead. OSC 9 (iTerm2), OSC 777 (rxvt
`notify`) and OSC 99 (kitty) together reach most terminals, and every surveyed terminal ignores
sequences it does not know. Codex CLI, Gemini CLI and Claude Code all took this route.

## Decision

After a run, [notify.py](../../src/pratfall/notify.py) writes one sequence to `/dev/tty`, chosen by
detecting the terminal from environment variables. Terminals without a notification sequence get
BEL. It sends exactly one sequence because kitty, foot, Konsole, WezTerm and Ghostty each accept
several formats, so sending all of them would show duplicates. `notify_method` overrides detection
for cases the environment cannot identify, such as SSH or a stale tmux environment. tmux and GNU
screen get their DCS passthrough wrapping. Zellij translates the sequences itself.

Notifications are on by default and gated so they stay quiet in scripts and quick runs. All of
these must hold:

- stderr is a terminal;
- the agent actually launched and was not interrupted;
- the run took at least `notify_after` seconds (default 10).

`notify`, `notify_method` and `notify_after` are ordinary `Options` fields. They follow the
existing precedence and provenance rules, with `--notify`/`--no-notify` as the CLI layer. Their
defaults are applied in `notify.py`, not in `resolve_profile`'s base layer, so existing `prat
profiles` output is unchanged for configs that never mention them.

The notification never touches stdout or stderr. It is sent after the result has been emitted,
and any failure in the notify path is swallowed so it cannot change output or the exit code. The
write ignores `SIGTTOU` so a background job is never stopped by `stty tostop`. Detection, encoding
and gating are pure functions of their inputs; `send` is the only I/O.

## Consequences

- Notification text is fixed and built from the agent label, status and duration. It never
  includes prompt text, so nothing sensitive reaches a lock screen or notification history. The
  text is still sanitized: control characters are replaced and `;` becomes `,`.
- Focus handling belongs to the terminal. iTerm2 and WezTerm show the notification while focused;
  only OSC 99 lets Pratfall request unfocused-only.
- tmux drops the sequence silently unless `allow-passthrough` is on. Pratfall cannot detect that.
- Detection misses fall back to BEL, and a user who wants silence there sets `notify = false`.
- OS notification commands and a user-configured notify command are not provided. A configured
  argv command, run without a shell, is the natural extension if one is needed.
- Native Windows is out of scope with the rest of the POSIX-only runner. Windows Terminal under
  WSL gets BEL until its OSC 777 support ships and is enabled.
