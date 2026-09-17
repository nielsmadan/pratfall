# Execution and adapters

Pratfall resolves one prompt, runs one native agent, and produces one normalized result. Native
protocol evidence and version-specific quirks live in the [agent references](reference/overview.md).

- [Dispatch boundary](#dispatch-boundary)
- [Bounded output](#bounded-output)
- [Completion and accounting](#completion-and-accounting)
- [Deadline, cleanup and diagnostics](#deadline-cleanup-and-diagnostics)
- [Extending an adapter](#extending-an-adapter)
- [Test isolation and evidence](#test-isolation-and-evidence)

## Dispatch boundary

- The CLI is a package: [parsing.py](../src/pratfall/cli/parsing.py) turns argv into a run or a
  management command, [dispatch.py](../src/pratfall/cli/dispatch.py) owns run and management
  dispatch, [doctor.py](../src/pratfall/cli/doctor.py) owns the inventory and version probes, and
  [presentation.py](../src/pratfall/cli/presentation.py) owns diagnostics writers and stdout
  emission. [dispatch.py](../src/pratfall/cli/dispatch.py) validates configuration, selected native
  options, and the child working directory before acquiring the prompt.
  [config.py](../src/pratfall/config.py) merges
  the global file with the invocation directory's `.pratfile`, or the local file selected by
  `--config`. Local profiles replace global profiles by name. Invocation options override profile
  options, local defaults, global defaults, and built-in defaults; all effective profiles are
  validated. Command paths, profile fields, and inherited defaults retain their defining file's
  location. Aliases of the global file load once through the selected local path, preserving that
  path's command base. The CLI selects one diagnostics writer per invocation and sends
  duplicate-profile warnings through it, before reading the prompt or launching an agent.
- [prompt_templates.py](../src/pratfall/prompt_templates.py) validates the restricted dollar
  placeholder syntax at config load and renders after base prompt acquisition. Template definitions
  retain their source files in immutable config records. Expansion size is checked before repeated
  substitution; rendered prompts use the same 1 MiB input limit. Unknown template names fail before
  input acquisition, while selected templates may supply the task without a base prompt.
- [prompt_input.py](../src/pratfall/prompt_input.py) reads invocation-relative regular files
  nonblocking under the shared input signal handlers. Context files are prepended after template
  rendering, preserving order, duplicate paths and content bytes. JSON-quoted label and separator
  space is reserved before any context files are read; each read uses the remaining aggregate
  1 MiB budget and final assembly stays within that bound. Explicit prompt plus stdin acquisition
  also limits the second read to its remaining budget.
- [catalog.py](../src/pratfall/catalog.py) holds immutable capabilities, not provider model lists.
  [`Adapter`](../src/pratfall/adapters/registry.py) is the adapter assembly point: command
  builder, one validator over the resolved profile, and exactly one of a whole-document decoder or
  an incremental consumer factory. The registry synthesizes the whole-document path for consumer
  adapters, so each agent has a single decode path rather than a precedence rule.
- [`run`](../src/pratfall/runner.py) owns POSIX process lifecycle and passes bytes to a
  schema-neutral consumer. Adapters own native protocol transitions; they do not manage processes.
  Its result carries either a `RawCapture` of complete stdout or a `ConsumedCapture` of the
  consumer's decoded output, so the selected capture mode is a type distinction rather than an
  empty-stdout sentinel.
- [`normalize`](../src/pratfall/output.py) selects status and exit code while retaining decoded
  output and accounting. The requested model remains distinct from models reported by the agent.
  [codes.py](../src/pratfall/codes.py) owns the public `Code` vocabulary and the fixed code-to-exit
  mapping; interruption, native signal and native exit stay computed in `normalize`.

The dependency boundary is acyclic, and runtime code uses only the standard library.
[test_layering.py](../tests/test_layering.py) machine-enforces that contract: the layer order, each
module's stated import allowances, absolute internal imports, and the stdlib-only runtime.
Invocation builders preserve argv execution and inherited native authentication and permissions.
Child stdin contains explicit bytes, including an empty input when appropriate; it never inherits
the terminal.

## Bounded output

[`JsonlConsumer`](../src/pratfall/consumer.py) owns strict UTF-8 and physical JSONL framing;
adapter consumers own event interpretation. The shared [`decode_with`](../src/pratfall/consumer.py)
convenience decoder feeds the same state machine used during execution. The registry identifies
which adapters use incremental consumption.

- Each physical event is limited to 8 MiB, excluding LF or CRLF. Whitespace and discarded events
  still obey this bound, but discarded output has no cumulative cap unless `--trace` captures it.
- `--trace` retains up to 8 MiB of complete native stdout while still feeding the adapter consumer.
- [`StateBudget`](../src/pratfall/consumer.py) allows 8 MiB of live retained state and 16,384
  logical records. Answer separators, identifiers, models, diagnostics, usage and cost snapshots
  count while retained. Replacement must refund the old payload. Numeric representations are
  bounded to 128 bytes.
- Adapters retain through [`Retention`](../src/pratfall/consumer.py) named slots rather than
  charging the budget by hand, so retaining a value and paying for it are one operation and
  replacement refunds automatically. `release` refunds a slot's bytes and record; `commit` stops
  tracking a slot, leaving its bytes charged permanently. `JsonlConsumer`
  owns the first-error-wins `malformed` policy and its retained diagnostic. A consumer that
  retains an answer it never charged fails the cross-adapter bound
  [conformance](../tests/test_adapter_contracts.py).
- Whole-document JSON and text adapters use the runner's 8 MiB complete stdout capture; native
  stderr is capped at 2 MiB. [`parse`](../src/pratfall/adapters/whole_json.py) re-checks
  that same `STDOUT_BYTES` document cap and supplies shared framing, Unicode, duplicate-key and
  numeric guards for every whole-document JSON adapter. The cap bounds input bytes, not the
  decoded object graph; a compact document expands severalfold in memory, and that transitive
  bound is the accepted ceiling.
- [limits.py](../src/pratfall/limits.py) owns every budget above: `STDOUT_BYTES`, `STDERR_BYTES`,
  `EVENT_BYTES`, `RETAINED_STATE_BYTES`, `RECORD_COUNT`, `NUMERIC_BYTES`. It also owns the
  `TERMINATE_GRACE` and `FINAL_DRAIN_GRACE` cleanup windows.

[OpenHands](reference/openhands.md) needs a separate bounded consumer for mixed SDK events and
native prose. Its trailing Rich summary is never reparsed as events; keep that exception out of
the strict shared JSONL framer.

All eight whole-document JSON adapters share one strictness rule: Claude, Cursor, Droid, Gemini,
Grok, OpenClaw, Reasonix and Vibe reject a duplicate object key, a nonfinite number, a numeric literal
wider than `NUMERIC_BYTES`, nesting deep enough to exhaust the parser, and an unpaired surrogate
anywhere in the document, including in fields that adapter never reads. The duplicate-key, numeric
and nesting guards run inside `parse`, while the nonfinite and unpaired-surrogate walk applies to a
successfully-shaped document, so a provider or protocol failure detected earlier takes precedence.
Claude, Cursor, Gemini and OpenClaw accepted all of those before 2026-09-14; one rule for eight
adapters was chosen over four divergent ones, and the tightening is deliberate rather than a
compatibility guarantee. Rejections name the underlying reason: `parse` reports the
standard-library decoder message, a duplicate object key, an over-wide numeric literal or excessive
nesting behind each agent's own message prefix.

## Completion and accounting

Structured adapters require their own native completion contract and recognize verified semantic
provider failures even after process exit zero. Text decoders remove only terminal CR/LF; native
banners and progress can remain in the returned answer.

Do not share terminal rules by schema resemblance: [Qwen](reference/qwen.md) and [Amp](reference/amp.md)
differ on repeated results. Native success alone can be insufficient, while empty text can be valid;
the [agent references](reference/overview.md) establish each contract.

[`normalize`](../src/pratfall/output.py) applies precedence in this order: interruption, runner
timeout, runner error, decoded native timeout, native signal or nonzero exit, decoder error, success.
Consequently, native nonzero exit takes precedence over ordinary protocol errors.

Populate nullable usage, `reported_models`, and `cost_usd` only from verified native mappings.
[`model_map`, `model` and `cost`](../src/pratfall/adapters/accounting.py) validate nonempty UTF-8
model identifiers and finite nonnegative USD costs. Keep valid accounting alongside provider,
protocol, runner and timeout failures; never substitute a reported model for the requested `model`.

Replacing accounting snapshots must refund retained state, and cumulative snapshots must not be
summed. See [OpenCode](reference/opencode.md) for per-step replacement and unknown totals, and
[Reasonix](reference/reasonix.md) for native field names that misrepresent token or currency units.

## Deadline, cleanup and diagnostics

The runner starts a new process group, drains stdout and stderr concurrently, and enforces one
monotonic run deadline. [`_terminate_and_drain`](../src/pratfall/runner.py) sends SIGTERM, then
SIGKILL after a bounded grace period, and verifies both parent reaping and group disappearance.
Repeated interruptions accelerate termination. Timeout/interruption cleanup feeds trailing bytes
before finalizing the consumer once; after a hard local output failure, stdout drains without
reparsing. [interruption.py](../src/pratfall/interruption.py) is the one signal seam: `handler_for`
builds a first/repeat handler over shared `InterruptionState`, and `install`, `restore` and the
`handling` context manager swap SIGINT and SIGTERM dispositions and always put the previous ones
back. The runner, prompt input and the version probes use it rather than installing handlers directly.

Cleanup covers the owned POSIX process group. Deliberately detached descendants and externally
managed server processes are outside that boundary. Ordinary successful completion preserves
native background-process behavior.

Encoding and presentation errors discovered after parent exit still invoke group cleanup through
[`_after_run_failure`](../src/pratfall/cli/dispatch.py): descendants can survive after closing the
pipes. An unexpected exception is handled twice for the same reason: once where the process result
is still bound, so cleanup runs, and once around the whole run as the last resort; both paths
report `internal_error` rather than a traceback. Both ordinary and progress diagnostics preserve the
decoded answer and accounting when stderr fails. Ordinary
diagnostics flush before final result emission; a failed stream is redirected to prevent another
flush at interpreter shutdown from replacing the normalized exit code. The progress sink is
deliberately never redirected: it writes with `os.write`, so no buffer survives to be flushed, and
redirecting it would restore the saved descriptor flags onto the replacement descriptor.
A zero-signal `killpg` probe returning EPERM keeps the group pending within the existing deadline;
persistent denial fails cleanup, and SIGTERM/SIGKILL permission errors remain explicit failures.
This handles macOS group teardown where a successful SIGTERM can precede a denied existence probe;
the [XNU implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_sig.c#L1709)
excludes zombie group members.
[`test_cleanup_rechecks_group_after_transient_probe_permission_error`](../tests/test_runner.py)
preserves this case.

stdout contains final text or one JSON result. Native stderr and Pratfall diagnostics use stderr.
One execution path emits the launch line, native stderr and the completion line through the
invocation's diagnostics writer, so progress is a choice of writer rather than a second run path.
Trace mode replays captured native stdout on stderr before the completion line.
Opt-in progress uses the static [`Activity`](../src/pratfall/models.py) category vocabulary, whose
labels live in `ACTIVITY_LABELS`, through the nonblocking
[`_ProgressDiagnostics`](../src/pratfall/cli/presentation.py) sink, which sets and restores stderr's
descriptor flags exactly once per invocation, covering config warnings and the run alike.
Backpressure drops progress, trace and diagnostic writes rather than delaying the run; other sink
failures enter normal cleanup and result handling.

[`_doctor`](../src/pratfall/cli/doctor.py) reuses the runner with a three-second deadline and
64 KiB per-stream bounds. The CLI selects opaque text and records per-agent probe errors;
the catalog owns probe arguments.

## Extending an adapter

Keep the builder, decoder and protocol state in the adapter; register its callables explicitly on
an [`Adapter`](../src/pratfall/adapters/registry.py) record with exactly one of `whole_document` or
`consumer`; supplying both or neither raises at import.
`validate` takes the resolved profile, so cross-field native rules stay in the adapter that owns
them; `build` assumes the CLI already validated at the trust boundary and never revalidates.
Use [`validate_flags`](../src/pratfall/adapters/native_args.py) with a finite flag set and known
arity. Reserve Pratfall-owned fields and transports; response files, positional arguments and
subcommands are rejected. Trusted executable wrappers cover unsupported options.

Check the relevant [native evidence](reference/overview.md) before exposing capabilities or
accounting; the [fast-mode mappings](reference/overview.md#shared-controls-and-accounting) show why native
config flags may need reserving. Exercise native completion, replacement budgets, malformed output
and cleanup through fake commands in the matching tests; those tests must not spend inference credits.

## Test isolation and evidence

Parser and consumer probes need explicit fake executable prefixes, isolated config/XDG paths,
and a restricted PATH. Calling the real parser can reach execution or `config init`; a parser-only
test intent does not isolate those effects. Specify expected argv and output independently of
production builders. Give the harness its own deadline and cleanup.

[Installed-package checks](release.md#check-installed-packages-locally) exercise the wheel's shared
Q/E scenarios and both wheel and sdist adapter scenarios. E13/E14 remain `Not run` in the installed
report because adversarial framing and stderr transport are covered by source tests. Per-agent
tests supply the full malformed-output and retained-state matrices;
[harness regressions](../scripts/test_qa_installed.py) exercise its assertions separately.
Fixture success establishes wrapper behavior against the documented protocol, not authenticated
vendor compatibility.

Lifecycle fixtures hold a descendant advisory lock and synchronize readiness before flooding or
signaling. Lock release must be observed after Prat completes and before emergency cleanup.
Input-signal checks wait for handler readiness through a harness-only bootstrap; progress checks
observe stderr while the native fake still waits for release. Fixed startup sleeps and observations
made after emergency cleanup cannot establish these properties.
