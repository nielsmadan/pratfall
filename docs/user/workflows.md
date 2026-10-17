# Workflows

A workflow is a named set of profile steps. Prat runs each step as its own `prat` invocation,
passes outputs downstream, and runs independent steps in parallel. Define workflows globally or in
a project's `.pratfile`.

- [Define a workflow](#define-a-workflow)
- [Order and input](#order-and-input)
- [Join and bind outputs](#join-and-bind-outputs)
- [Fan out over items](#fan-out-over-items)
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
| `each` | Name of an ancestor step whose output is split into items; the step runs once per item. See [fan out over items](#fan-out-over-items). |
| `split` | With `each`: `"lines"` (the default) or `"json"`. |
| `as` | With `each`: a prompt parameter of the step's profile that receives the item. |
| `max_parallel` | With `each`: how many instances run at once, 1 to 64, or `"all"` for 64. Defaults to 4. |
| `max_items` | With `each`: the most items the step may run, 1 to 4096. Defaults to 4096. |
| `overflow` | With `each`: `"error"` (the default) fails the step when there are more than `max_items` items; `"truncate"` runs the first `max_items` and drops the rest. |

A workflow name follows the profile rules: letters, digits, underscores or hyphens. It cannot equal
a profile name, a built-in agent name or alias, or a management command, including `workflows`.
A local workflow replaces a global one of the same name, with a warning on stderr, as for profiles.
`prat config validate` checks every workflow without launching an agent: step names, `after` and
`bind` references, unknown selectors, and that a step with no prompt and `input = "none"` can
never receive input. For an `each` step it also checks that the source is an ancestor and not
itself an `each` step, that `as` is a prompt parameter of the profile and not in `bind`, and that
`input = "none"` comes with `as`. `prat workflows` lists each workflow's steps, parameters and
source, and `prat workflows --json` returns the same fields. In the text listing an `each` step
adds `each=SOURCE`, and `split=json`, `as=NAME`, `max_parallel=N`, `max_items=N` and
`overflow=truncate` when they differ from the defaults.

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

## Fan out over items

`each` runs a step once per item of an ancestor step's output. The source can be any ancestor, not
only a step in `after`, but it cannot itself be an `each` step. `split`, `as`, `max_parallel`,
`max_items` and `overflow` are rejected without `each`.

```toml
[workflows.review-files]
steps = [
  { name = "list", profile = "file-lister" },
  { name = "review", profile = "review-one", each = "list", as = "file", max_parallel = 2 },
]
```

Here `list` prints one path per line and `review` runs once per path, with the path filling
`$file` in the `review-one` prompt, two at a time.

### Split the output

With `split = "lines"`, the default, each line of the source output is an item. Lines are
stripped, and empty lines are dropped.

With `split = "json"` the source must produce a JSON array. Prat uses the source's
[structured output](json.md#run-results) when that is an array; otherwise it parses the output
text, and then the content of its first fenced code block. Anything else fails the `each` step
with `invalid_arguments`, as does a document containing `NaN`, `Infinity` or `-Infinity`. Each
element becomes one item:

- a string is used as is, and an empty or whitespace-only string is dropped;
- `null` is dropped;
- an array or object becomes compact JSON text, with non-ASCII characters kept;
- a number or boolean becomes its JSON serialization (`1e2` becomes `100.0`).

An empty list is not a failure: the step succeeds with empty output and no instances, and a step
after it that has no other input is handled as for any empty upstream output.

### Limit the items

Items are counted after empty and `null` ones are dropped. `max_items` sets how many the step may
run, 4096 at most and by default. With `overflow = "error"`, the default, a longer list fails the
`each` step with `invalid_arguments` before any instance launches: `output of step 'SOURCE' has N
items, more than the M item limit.`

With `overflow = "truncate"`, the first `max_items` items run and the rest are dropped. Prat writes
one line to stderr when it cuts the list:

```text
prat: workflow 'review-files' step 'review': running the first 20 of 57 items.
```

The stderr line is a best-effort notice and can be lost when stderr is blocked. The step's JSON entry
reports the number of dropped items as [`dropped`](json.md#workflow-results), and that field is the
record.
Dropped items get no instance and no `items` entry.

```toml
{ name = "review", profile = "review-one", each = "list", as = "file",
  max_parallel = "all", max_items = 20, overflow = "truncate" }
```

### What an instance receives

By default the item is the instance's input, so it fills `$input` or follows the prompt as for any
[step input](#order-and-input). With `as`, the item fills the named prompt parameter instead. That
parameter is removed from the workflow's own parameters, as for `bind`, and cannot also appear in
`bind`. The instance's input is then the joined output of its other `after` steps, without the
`each` source.

Because the item is the only input without `as`, `input = "none"` on an `each` step needs `as`.
An instance with no prompt and no input, or whose input, item and bound values exceed the 1 MiB
prompt limit, fails with `invalid_arguments`.

### Labels and joined output

Each item has a label for diagnostics and results. An item that is unique in the list, at most 80
characters, and free of control characters (so a single line) is its own label. Any other item,
including a duplicate or one that looks like `#3`, is labeled `#N` with its 1-based position, so
labels are unique. Stderr relayed from an instance is prefixed `[STEP[LABEL]] `.

The `each` step's output joins its instances' outputs in item order by the
[usual rules](#join-and-bind-outputs), with `## STEP[LABEL]` headings when more than one output is
non-empty. Usage, cost and duration are summed over the instances. If the joined output exceeds
64 MiB, the step fails with `stdout_limit_exceeded`.

### The queue

At most `max_parallel` instances of an `each` step run at once, 4 by default and 1 to 64 (`"all"`
means 64); the rest wait and start in item order as slots free up. Different `each` steps and
ordinary steps run alongside each other as for other steps. With `parallel = false`, one instance
or step runs at a time, whatever `max_parallel` says: the workflow runs the next ready step in
declared order, and within an `each` step the next item.

### Failed and interrupted items

The first failed instance stops new launches across the workflow, as for a failed step. Instances
already running finish. The `each` step reports the failed instance's status and exit code, with
the error `item 'LABEL': MESSAGE`, and the workflow error reads `step 'NAME': item 'LABEL':
MESSAGE`. Items that never started are listed with `status: "skipped"`.

An `each` step that is cut short by another step's failure, with none of its own instances failed,
is reported `skipped`, not as a failure. If an instance of the `each` step was running when the
workflow was interrupted, the step is reported `interrupted`. Otherwise it is reported `skipped`:
with its item entries when its items were already computed, or as never started,
`{"step": NAME, "status": "skipped", "items": []}`, when it had not begun.

## Fill parameters

The workflow's parameters are the prompt parameters of its steps' profiles, minus any step's `bind`
and any `each` step's `as`, in order of first appearance. A name used by several steps is one
parameter and fills all of them. Words after the workflow name and `--param NAME=VALUE` fill
parameters exactly as for
a [profile prompt](running.md#fill-prompt-parameters). Missing, unknown and repeated
parameters fail before any step starts. A step whose agent selector has no profile prompt takes no parameters.

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

The JSON results returned by all steps and instances of one workflow share a 64 MiB budget. The
step or instance whose result crosses it fails with `stdout_limit_exceeded`, which stops new
launches like any failure. Because the budget is shared, a large `each` step cannot keep growing
Prat's memory or the workflow result with every instance.

`--timeout` applies to each step, not to the workflow. A step that is still running after its
timeout plus twice the termination grace plus 10 seconds is torn down and reported as `timeout`.

SIGINT, SIGTERM and SIGHUP stop the workflow. Prat forwards the signal to each running step (SIGHUP
as SIGTERM), starts no further step, and exits with `128 + signal` and status `interrupted`. A
SIGHUP that was already ignored when `prat` started stays ignored, so `nohup prat NAME` survives a
hangup. If the workflow process dies without cleaning up, its steps notice the parent has gone and
terminate themselves.

## Output and JSON

Text mode writes the combined output of the final steps, the steps no other step comes after, to
stdout, joined as above when there are several. If that joined output exceeds 64 MiB, the
workflow fails with `stdout_limit_exceeded` and the error `final output: joined output exceeds the
67108864 byte limit.` On failure stdout is empty and one line goes to stderr:

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
the parameters it receives, without launching an agent. An `each` step also shows `each=`,
`split=` and `max_parallel=` always, `as=` when set, and `max_items=` and `overflow=truncate` when
they differ from the defaults. The items do not exist yet, so nothing is split:

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

Each step is a fresh `prat` process that loads configuration from disk when it starts. Editing
the global config while a workflow runs changes steps that have not started yet. The local file
(`.pratfile` or `--config PATH`) is pinned to the SHA-256 digest seen at launch: if a step's agent
or anything else rewrites it, every later step fails with `invalid_config`, so a step cannot plant
a command for the next one. A rewritten `.pratfile` reports `local config changed since it was
trusted`, because the trust check runs first; a rewritten `--config` file reports `local config
changed since the workflow started`.
Parameters, timeouts and the step list were resolved at launch and do not change.
