# Running prompts

Run exactly one prompt through a built-in selector or named profile:

```sh
prat cx "review this change"
prat simple --effort low "rebase these commits"
printf 'multiline\nprompt\n' | prat cc -
prat cc --prompt=-leading-dash
```

Use `--prompt=TEXT` when prompt text begins with a dash. A lone `-` reads one UTF-8 prompt from
stdin. Prompts must be nonempty, contain no NUL bytes, and remain within 1 MiB. Gemini, Copilot,
Cursor, and Kiro carry prompts in argv and can hit an operating-system argv limit below 1 MiB.

Run options can appear before or after the selector. `--model`, `--effort`, `--timeout`,
`--max-budget-usd`, `--max-turns`, and `--max-ai-credits` override supported profile fields.
`--cwd PATH` changes the child working directory. `--dry-run` validates and prints the resolved argv
without launching the agent or revealing the prompt.

Arguments after `--` are trusted native argv and replace configured `native_args`. Pratfall accepts
only a finite documented option set and rejects native prompt, output, session, cwd, model, effort,
and budget controls that it owns. Unsupported options can be placed in a trusted executable wrapper.

Pratfall preserves native permission defaults. It does not retry, fall back to another agent or
model, install or log into commands, or edit configuration. Hermes `--run-budget` is a native
wall-clock wrap-up budget; Pratfall's `--timeout` remains the outer process deadline.
