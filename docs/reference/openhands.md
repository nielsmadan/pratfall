# OpenHands 1.16.0 / SDK 1.21.0

**Evidence recorded:** 2026-09-11.
**Method:** Static primary-source and package inspection; native agents were not executed.

## Native contract

The [CLI manifest](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/pyproject.toml)
pins `openhands-sdk==1.21.0`. The
[parser](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/argparsers/main_parser.py)
accepts argparse string `--task`; Prat sends `--headless --json --task=PROMPT`, with empty stdin.
The equals form protects dash-leading and multiline prompt data. The
[prompt helper](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/utils.py)
uses the task directly; native `--file` adds context instructions and therefore cannot transport
Prat's input file unchanged. OS argv limits can reject a prompt below Prat's input limit.

The [entrypoint](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/entrypoint.py)
and [terminal compatibility check](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/terminal_compat.py)
print a fixed non-TTY warning and `TTY_INTERACTIVE` override hint to stdout even in headless mode.
[Setup](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/setup.py)
prints `Initializing agent...`, optional `✓ Hooks loaded`, and `✓ Agent initialized with model:`
followed by the native model. Prat discards this banner and any Rich-wrapped model continuation
lines until the next SDK record or known status line; it never uses banner text as model accounting.
The [environment warning](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/stores/agent_store.py)
uses stderr. The entrypoint prints goodbye, conversation ID and resume hints after the summary;
these remain in the discarded summary section. Unexpected startup failures still fail through
native exit or missing terminal evidence.

The same helper prints `json.dumps(event.model_dump())` as individual lines. The
[runner](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/tui/core/conversation_runner.py)
also prints `Agent is working` and `Agent finished`. The
[headless app](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/tui/textual_app.py)
then prints a Rich rule headed `CONVERSATION SUMMARY`, counts, and a panel containing arbitrary
last-message text. JSON mode does not suppress that summary. Prat accepts the verified initialization and status framing
and discards the bounded summary section beginning with its rule, without parsing echoed panel
text as SDK events. Other prose before the summary is a protocol error. Every physical line,
including discarded prose, has the existing event-byte bound; retained text and diagnostics share
StateBudget. This framing is confined to the OpenHands consumer.

[SDK response dispatch](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/agent/response_dispatch.py)
finishes on `kind: MessageEvent`, `source: agent`, `llm_message.role: assistant`, with at least one
nonblank text block. Prat concatenates `llm_message.content` blocks having `type: text` and string
`text`, excluding image/reasoning data. The dispatcher's no-content handler also emits well-formed
empty or reasoning-only agent messages before a corrective user message and continued work.
Validated messages without nonblank text are nonterminal; a later terminal event is still required
at EOF. Malformed assistant content remains a protocol error. Alternatively,
[FinishAction](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/tool/builtins/finish.py)
is terminal: `kind: ActionEvent`, `source: agent`, `tool_name: finish`,
`action: {kind: FinishAction, message: STRING}`. A second terminal event is a protocol error;
first terminal text survives. Finish can itself describe inability to perform a task, so Prat
makes no success judgment from generated prose. Native exit zero alone is insufficient.

[ConversationErrorEvent](https://github.com/OpenHands/software-agent-sdk/blob/v1.21.0/openhands-sdk/openhands/sdk/event/conversation_error.py)
has `code` and `detail` strings and indicates a conversation-level failure, even when the CLI
catches the SDK exception and exits zero. It outranks protocol errors and retains earlier text.
`AgentErrorEvent` is a recoverable tool observation. Other known SDK kinds (system, observation,
state, token, streaming, condensation, hook, completion-log, pause and ACP events) carry no final
answer here. Unknown kinds and malformed relevant fields fail closed.

The only accepted native option is `--override-with-envs` (arity zero), verified in the
[shared parser](https://github.com/OpenHands/OpenHands-CLI/blob/1.16.0/openhands_cli/argparsers/util.py).
It selects already-configured native environment model/auth settings. There is no direct model,
effort, fast or normalized budget flag. Prompt/file, headless/JSON, session/resume/last, config,
confirmation and administrative selectors are reserved or rejected. Headless forces `NeverConfirm`
and disables the critic; Prat preserves that automatic approval behavior. Existing native setup is
required; this adapter neither performs setup nor changes native settings.

## Implementation

- [Pratfall adapter](../../src/pratfall/adapters/openhands.py)
- [Shared execution boundary](../execution.md)
- [Interface comparison and evidence scope](overview.md)
