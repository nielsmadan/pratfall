# 0002 — Model an adapter as a record of callables

**Status:** accepted

**Recorded:** 2026-09-14

## Context

Pratfall supports twenty-two native agents. Each has its own argv shape, its own cross-field
validation rules, and its own completion contract. The parts that look shared are the parts that
differ: [execution and adapter boundaries](../execution.md) warns against sharing terminal rules by
schema resemblance, because Qwen and Amp disagree on repeated results. A Protocol, an abstract base
class, or a base class every agent subclasses would each invite a maintainer to lift such a rule
into one inherited place.

## Decision

Define `Adapter` in [registry.py](../../src/pratfall/adapters/registry.py) as a frozen dataclass of
functions: `build`, `validate`, and exactly one of `whole_document` or `consumer`. Keep one entry
per agent in the immutable `ADAPTERS` mapping. Let `__post_init__` derive `decode` and raise at
import when both or neither decoding field is supplied. Agent modules stay plain modules exposing
module-level functions, with no adapter base class and no inheritance from the seam. Where a
*callable* contract is genuinely needed, use a Protocol: `ConsumerFactory` in
[consumer.py](../../src/pratfall/consumer.py).

## Consequences

- The registry is the single assembly point; adding an agent is one module and one entry, reviewable in one place.
- Exclusivity is enforced at import, so each agent has one decode path instead of a runtime precedence rule; the registry synthesizes the whole-document decoder for consumer adapters through `decode_with`.
- Nothing is inherited, so no adapter can acquire a terminal or accounting rule it did not state for itself.
- Shared behavior costs an explicit import from `accounting`, `native_args` or `whole_json`, and a change that genuinely applies to every agent is twenty-two edits rather than one.
- A reader cannot find "the adapter interface" by looking for subclasses; the registry is the only listing.
