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
  working directory before acquiring the prompt. [config.py:220](../src/pratfall/config.py#L220)
  resolves invocation options over profile options, global defaults, and built-in defaults;
  all configured profiles are validated.
- [catalog.py](../src/pratfall/catalog.py) holds immutable capabilities, not provider model lists.
  [registry.py:42](../src/pratfall/adapters/registry.py#L42) is the adapter assembly point: command
  builder, validator, decoder, and optional incremental consumer factory.
- [runner.py:65](../src/pratfall/runner.py#L65) owns POSIX process lifecycle and passes bytes to a
  schema-neutral consumer. Adapters own native protocol transitions; they do not manage processes.
- [output.py:7](../src/pratfall/output.py#L7) selects status and exit code while retaining decoded
  output and accounting. The requested model remains distinct from models reported by the agent.

The dependency boundary is acyclic, and runtime code uses only the standard library. Invocation
builders preserve argv execution and inherited native authentication and permissions. Child stdin
contains explicit bytes, including an empty input when appropriate; it never inherits the terminal.

## Bounded output

[JsonlConsumer:94](../src/pratfall/consumer.py#L94) owns strict UTF-8 and physical JSONL framing;
adapter consumers own event interpretation. Its convenience decoder feeds the same state machine
used during execution. The registry identifies which adapters use incremental consumption.

- Each physical event is limited to 8 MiB, excluding LF or CRLF. Whitespace and discarded events
  still obey this bound, but discarded output has no cumulative trace cap.
- [StateBudget:39](../src/pratfall/consumer.py#L39) allows 8 MiB of live retained state and 16,384
  logical records. Answer separators, identifiers, models, diagnostics, usage and cost snapshots
  count while retained. Replacement must refund the old payload. Numeric representations are
  bounded to 128 bytes.
- Whole-document JSON and text adapters use the runner's 8 MiB complete stdout capture; native
  stderr is capped at 2 MiB. [whole_json.py:8](../src/pratfall/adapters/whole_json.py#L8) supplies
  shared framing, Unicode, duplicate-key and numeric guards for Reasonix, Droid and Vibe.

[OpenHands](reference/openhands.md) needs a separate bounded consumer for mixed SDK events and
native prose. Its trailing Rich summary is never reparsed as events; keep that exception out of
the strict shared JSONL framer.

Known deferred parser limits from the 2026-09-09 review remain in the older whole-document
decoders: extreme JSON nesting can escape Claude, Gemini, OpenClaw and Cursor as `RecursionError`;
Cursor also lacks an oversized-integer `ValueError` guard. Extreme TOML integers can escape
[configuration error handling](../src/pratfall/config.py#L207). The byte caps do not prevent
these crafted-input failures.

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
monotonic run deadline. [Cleanup:317](../src/pratfall/runner.py#L317) sends SIGTERM, then SIGKILL after
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
