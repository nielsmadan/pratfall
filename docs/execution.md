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

- [cli.py](../src/pratfall/cli.py) validates configuration, selected native options, and the child
  working directory before acquiring the prompt. [config.py](../src/pratfall/config.py) merges
  the global file with the invocation directory's `.pratfile`, or the local file selected by
  `--config`. Local profiles replace global profiles by name. Invocation options override profile
  options, local defaults, global defaults, and built-in defaults; all effective profiles are
  validated. Command paths, profile fields, and inherited defaults retain their defining file's
  location. Aliases of the global file load once through the selected local path, preserving that
  path's command base. The CLI sends duplicate-profile warnings to stderr before reading the prompt
  or launching an agent, using its nonblocking sink when progress is enabled.
- [catalog.py](../src/pratfall/catalog.py) holds immutable capabilities, not provider model lists.
  [registry.py:41](../src/pratfall/adapters/registry.py#L41) is the adapter assembly point: command
  builder, one validator over the resolved profile, and exactly one of a whole-document decoder or
  an incremental consumer factory. The registry synthesizes the whole-document path for consumer
  adapters, so each agent has a single decode path rather than a precedence rule.
- [runner.py:62](../src/pratfall/runner.py#L62) owns POSIX process lifecycle and passes bytes to a
  schema-neutral consumer. Adapters own native protocol transitions; they do not manage processes.
- [output.py:7](../src/pratfall/output.py#L7) selects status and exit code while retaining decoded
  output and accounting. The requested model remains distinct from models reported by the agent.
  [codes.py](../src/pratfall/codes.py) owns the public `Code` vocabulary and the fixed code-to-exit
  mapping; interruption, native signal and native exit stay computed in `normalize`.

The dependency boundary is acyclic, and runtime code uses only the standard library. Invocation
builders preserve argv execution and inherited native authentication and permissions. Child stdin
contains explicit bytes, including an empty input when appropriate; it never inherits the terminal.

## Bounded output

[JsonlConsumer:91](../src/pratfall/consumer.py#L91) owns strict UTF-8 and physical JSONL framing;
adapter consumers own event interpretation. Its convenience decoder feeds the same state machine
used during execution. The registry identifies which adapters use incremental consumption.

- Each physical event is limited to 8 MiB, excluding LF or CRLF. Whitespace and discarded events
  still obey this bound, but discarded output has no cumulative trace cap.
- [StateBudget:36](../src/pratfall/consumer.py#L36) allows 8 MiB of live retained state and 16,384
  logical records. Answer separators, identifiers, models, diagnostics, usage and cost snapshots
  count while retained. Replacement must refund the old payload. Numeric representations are
  bounded to 128 bytes.
- Adapters retain through `Retention` named slots rather than charging the budget by hand, so
  retaining a value and paying for it are one operation and replacement refunds automatically.
  `release` returns a slot's bytes and record; `commit` makes a slot permanent. `JsonlConsumer`
  owns the first-error-wins `malformed` policy and its retained diagnostic. A consumer that
  retains an answer it never charged fails the cross-adapter bound
  [conformance](../tests/test_adapter_contracts.py).
- Whole-document JSON and text adapters use the runner's 8 MiB complete stdout capture; native
  stderr is capped at 2 MiB. [whole_json.py:8](../src/pratfall/adapters/whole_json.py#L8) re-checks
  that same `STDOUT_BYTES` document cap and supplies shared framing, Unicode, duplicate-key and
  numeric guards for every whole-document JSON adapter.
- [limits.py](../src/pratfall/limits.py) owns every budget above: `STDOUT_BYTES`, `STDERR_BYTES`,
  `EVENT_BYTES`, `RETAINED_STATE_BYTES`, `RECORD_COUNT`, `NUMERIC_BYTES`.

[OpenHands](reference/openhands.md) needs a separate bounded consumer for mixed SDK events and
native prose. Its trailing Rich summary is never reparsed as events; keep that exception out of
the strict shared JSONL framer.

All seven whole-document JSON adapters share one strictness rule: Claude, Cursor, Droid, Gemini,
OpenClaw, Reasonix and Vibe reject a duplicate object key, a nonfinite number, a numeric literal
wider than `NUMERIC_BYTES`, nesting deep enough to exhaust the parser, and an unpaired surrogate
anywhere in the document, including in fields that adapter never reads. The duplicate-key, numeric
and nesting guards run inside `parse`, while the nonfinite and unpaired-surrogate walk applies to a
successfully-shaped document, so a provider or protocol failure detected earlier takes precedence.
Claude, Cursor, Gemini and OpenClaw accepted all of those before 2026-09-14; one rule for seven
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

[normalize:7](../src/pratfall/output.py#L7) applies precedence in this order: interruption, runner
timeout, runner error, decoded native timeout, native signal or nonzero exit, decoder error, success.
Consequently, native nonzero exit takes precedence over ordinary protocol errors.

Populate nullable usage, `reported_models`, and `cost_usd` only from verified native mappings.
[accounting.py:7](../src/pratfall/adapters/accounting.py#L7) validates nonempty UTF-8 model identifiers
and finite nonnegative USD costs. Keep valid accounting alongside provider, protocol, runner and
timeout failures; never substitute a reported model for the requested `model`.

Replacing accounting snapshots must refund retained state, and cumulative snapshots must not be
summed. See [OpenCode](reference/opencode.md) for per-step replacement and unknown totals, and
[Reasonix](reference/reasonix.md) for native field names that misrepresent token or currency units.

## Deadline, cleanup and diagnostics

The runner starts a new process group, drains stdout and stderr concurrently, and enforces one
monotonic run deadline. [Cleanup:314](../src/pratfall/runner.py#L314) sends SIGTERM, then SIGKILL after
a bounded grace period, and verifies both parent reaping and group disappearance. Repeated
interruptions accelerate termination. Timeout/interruption cleanup feeds trailing bytes before
finalizing the consumer once; after a hard local output failure, stdout drains without reparsing.

Cleanup covers the owned POSIX process group. Deliberately detached descendants and externally
managed server processes are outside that boundary. Ordinary successful completion preserves
native background-process behavior.

Encoding and presentation errors discovered after parent exit still invoke
[group cleanup](../src/pratfall/cli.py#L654): descendants can survive after closing the pipes.
Both ordinary and progress diagnostics preserve the decoded answer and accounting when stderr
fails. Ordinary diagnostics flush before final result emission; a failed stream is redirected to
prevent another flush at interpreter shutdown from replacing the normalized exit code.
A zero-signal `killpg` probe returning EPERM keeps the group pending within the existing deadline;
persistent denial fails cleanup, and SIGTERM/SIGKILL permission errors remain explicit failures.
This handles macOS group teardown where a successful SIGTERM can precede a denied existence probe;
the [XNU implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_sig.c#L1709)
excludes zombie group members. [Regression coverage](../tests/test_runner.py#L21) preserves this case.

stdout contains final text or one JSON result. Native stderr and Pratfall diagnostics use stderr.
Opt-in progress uses static activity categories through a
[nonblocking sink:602](../src/pratfall/cli.py#L602), which also handles launch, completion and native
diagnostics in that mode and restores stderr's descriptor flags. Backpressure drops writes rather
than delaying the run; other sink failures enter normal cleanup and result handling.

[Version diagnostics:262](../src/pratfall/cli.py#L262) reuse the runner with a three-second deadline
and 64 KiB per-stream bounds. The CLI selects opaque text and records per-agent probe errors;
the catalog owns probe arguments.

## Extending an adapter

Keep the builder, decoder and protocol state in the adapter; register its callables explicitly.
`validate` takes the resolved profile, so cross-field native rules stay in the adapter that owns
them; `build` assumes the CLI already validated at the trust boundary and never revalidates.
Use [native_args.py:15](../src/pratfall/adapters/native_args.py#L15) with a finite flag set and known
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
