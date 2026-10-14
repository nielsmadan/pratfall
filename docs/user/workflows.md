# Workflows

A workflow is a named set of profile steps. Prat runs each step as its own `prat` invocation,
passes outputs downstream, and runs independent steps in parallel. Define workflows globally or in
a project's `.pratfile`.

- [Define a workflow](#define-a-workflow)
- [Order and input](#order-and-input)
- [Join and bind outputs](#join-and-bind-outputs)
- [Fill parameters](#fill-parameters)
- [Parallelism and the working directory](#parallelism-and-the-working-directory)
- [Failure and interruption](#failure-and-interruption)
- [Output and JSON](#output-and-json)
- [Preview a workflow](#preview-a-workflow)
- [Allowed flags](#allowed-flags)
- [Configuration changes during a run](#configuration-changes-during-a-run)

## Define a workflow

```toml
version = 1

[profiles.draft]
agent = "claude"
prompt = "Write a short summary of $topic."

[profiles.critique]
agent = "codex"
prompt = "List factual errors in the text above."

[profiles.polish]
agent = "gemini"
prompt = "Rewrite the text above for clarity."

[profiles.merge]
agent = "claude"
prompt = "Combine the critique and the rewrite into one final text."

[workflows.write]
steps = [
  { name = "draft", profile = "draft" },
  { name = "critique", profile = "critique" },
  { name = "polish", profile = "polish", after = ["draft"] },
  { name = "merge", profile = "merge", after = ["critique", "polish"] },
]
```

```sh
prat write "tide pools"
prat workflows
```

`[workflows.NAME]` takes `steps`, a nonempty array of step tables, and `parallel`, a boolean that
defaults to `true`. A step takes:

| Key | Meaning |
| --- | --- |
| `name` | Required. Letters, digits, underscores or hyphens, starting with a letter; unique within the workflow. |
| `profile` | Required. A profile or built-in agent selector. A workflow cannot be a step. |
| `after` | Names of steps declared earlier that must succeed first. Defaults to the previous step, or none for the first step. `after = []` makes a root step. |
| `input` | `"none"` starts the step without upstream input. Any other value is rejected. |
| `bind` | A table mapping a prompt parameter of the step's profile to an ancestor step whose output fills it. |

A workflow name follows the profile rules: letters, digits, underscores or hyphens. It cannot equal
a profile name, a built-in agent name or alias, or a management command, including `workflows`.
A local workflow replaces a global one of the same name, with a warning on stderr, as for profiles.
`prat config validate` checks every workflow without launching an agent: step names, `after` and
`bind` references, unknown selectors, and that a step with no prompt and `input = "none"` can
never receive input. `prat workflows` lists each workflow's steps, parameters and source, and
`prat workflows --json` returns the same fields.

## Order and input

A step starts once every step in its `after` list has succeeded. Because `after` defaults to the
previous step, a workflow without `after` keys is a chain.

A step's input is the output of its `after` steps. It is the step's base prompt, so it fills
`$input` in the profile's prompt, or follows the prompt after two newlines when there is none, as
for [templates](running.md#apply-a-template). A root step without `input = "none"` takes the workflow's
own input, which is `--file`, `--prompt` or redirected stdin on the `prat` command line. A
workflow in which no step takes that input rejects it with "takes no input."

## Join and bind outputs

A step with several `after` steps receives their non-empty outputs, in `after` order. With one
non-empty output the text is passed unchanged. With several, each is introduced by a heading with
the step name:

```text
## critique

...critique output...

## polish

...polish output...
```

One trailing newline is removed from each output, and empty or whitespace-only outputs are
dropped. A step that
receives no input and has no prompt fails with `invalid_arguments` before it launches.

`bind` passes an ancestor's output as a named parameter instead of as input. Bound parameters are
removed from the workflow's own parameters, and the bound step is left out of the step's joined
input. A bound output with trailing newlines removed fills the parameter literally. Binding to an
empty output fails the step with `invalid_arguments`, as does an input plus bound values over the
1 MiB prompt limit.

```toml
{ name = "check", profile = "verify", after = ["clean", "pack"], bind = { report = "pack" } }
```

## Fill parameters

The workflow's parameters are the prompt parameters of its steps' profiles, minus any step's
`bind`, in order of first appearance. A name used by several steps is one parameter and fills
all of them. Words after the workflow name and `--param NAME=VALUE` fill parameters exactly as for
a [profile prompt](running.md#fill-prompt-parameters). Missing, unknown and repeated parameters
fail before any step starts. A step whose agent selector has no profile prompt takes no parameters.

## Parallelism and the working directory

With `parallel = true`, every step whose `after` steps have succeeded starts at once, and
children are launched in declared order. With `parallel = false`, one step runs at a time, in
declared order among the ready steps.

Parallel steps share one working directory. Two steps that edit files can overwrite each other's
changes, and Prat does not detect it. Run editing steps in sequence, give parallel steps
read-only tools, or select separate trees with each profile's own settings.

## Failure and interruption

The first failed step stops new launches. Steps already running finish normally, and steps that
never started are reported as `skipped`. The workflow reports the first failed step's status and
exit code, and its error is `step 'NAME': MESSAGE`. There is no retry and no fallback, as for a
single run.

`--timeout` applies to each step, not to the workflow. A step that is still running after its
timeout plus twice the termination grace plus 10 seconds is torn down and reported as `timeout`.

SIGINT, SIGTERM and SIGHUP stop the workflow. Prat forwards the signal to each running step (SIGHUP
as SIGTERM), starts no further step, and exits with `128 + signal` and status `interrupted`. A
SIGHUP that was already ignored when `prat` started stays ignored, so `nohup prat NAME` survives a
hangup. If the workflow process dies without cleaning up, its steps notice the parent has gone and
terminate themselves.

## Output and JSON

Text mode writes the combined output of the final steps, the steps no other step comes after, to
stdout, joined as above when there are several. On failure stdout is empty and one line goes to
stderr:

```text
prat: workflow 'write' failed at step 'critique': Codex exited with status 3.
```

Each step's stderr is relayed to Prat's stderr one line at a time with a `[STEP] ` prefix.
Very long lines are split at 64 KiB, and relayed lines are dropped rather than delaying the run
when stderr is blocked.

`--json` writes one object with a `steps` array holding every declared step's result, with
`status: "skipped"` for steps that never ran. See [workflow results](json.md#workflow-results)
for the fields. Usage and cost are the sums of the steps that report them.

## Preview a workflow

`--dry-run` resolves parameters and prints each step's profile, `after`, input mode, bindings and
the parameters it receives, without launching an agent:

```sh
prat write "tide pools" --dry-run
```

```text
draft: profile=draft after=none input=upstream parameters=topic="tide pools"
critique: profile=critique after=draft input=upstream
polish: profile=polish after=draft input=upstream
merge: profile=merge after=critique,polish input=upstream
```

Before a real run, and for `--dry-run`, Prat checks the flags, the parameters, each step's
timeout, the working directory and the base input. It does not check that an agent command exists
or is authenticated, that a step's native arguments are accepted, that a profile's schema,
instruction or path settings can be read, or that outputs will fit the prompt limit; those fail
when the step runs.

## Allowed flags

A workflow accepts `--param`, `-f`/`--file`, `--prompt`, `--config`, `--cwd`, `--timeout`,
`--json`, `--dry-run`, `--progress`, `--trace`, `--notify` and `--no-notify`. Every other flag, and
native arguments after `--`, fails with "FLAG is not supported for workflows." Set model, effort,
tools and other options in the step profiles. `--config`, `--cwd`, `--timeout`, `--progress` and
`--trace` are passed to every step.

A malformed flag value fails in the argument parser before Prat knows the selector is a workflow,
so its JSON error has the [plain run shape](json.md#run-results) rather than the workflow shape.

The completion notification, when enabled, names the workflow and fires once when it ends; steps
never notify themselves.

## Configuration changes during a run

Each step is a fresh `prat` process that loads configuration from disk when it starts. Editing a
profile or `.pratfile` while a workflow runs changes steps that have not started yet. Parameters,
timeouts and the step list were resolved at launch and do not change.
