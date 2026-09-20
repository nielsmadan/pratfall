# Cursor

**Evidence recorded:** 2026-09-09, with accounting updates on 2026-09-10.
**Method:** Source inspection and native observations where explicitly described below.

**Baseline:** Published Cursor 2026.09.08 package.

## Native contract

JSON print mode succeeds with one `type: result`, `subtype: success`, `is_error: false` object and a
string `result`. The docs state that failures exit nonzero and need not emit well-formed JSON, so
native exit status takes precedence over decoder failure. Published package source confirms
Commander's end-of-options handling. Prat supplies global print/model options first, then the
explicit `agent` command and `--` before the prompt; command-like text such as `login` and flags such
as `--force` remain data. No token usage is documented, and the argv prompt inherits OS size limits.
Whole-document parsing is bounded and rejects duplicate keys, excessive numeric width/nesting,
nonfinite numbers and invalid Unicode anywhere in the document.

## Session id (verified 2026-09-19)

The documented single-JSON success object carries `session_id` beside `result` and
`duration_ms`. Prat reports it as `native_session_id` when it is a string and ignores any other
shape, matching how the rest of that envelope is read. Cursor accepts no requested session id.

## Sources

- [Parameters](https://cursor.com/docs/cli/reference/parameters)
- [output format](https://cursor.com/docs/cli/reference/output-format)

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/cursor.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
