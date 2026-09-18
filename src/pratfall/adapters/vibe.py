from pratfall.adapters.native_args import Flag, validate_flags, validate_tool_values
from pratfall.adapters.whole_json import document_error, encoding_error, parse
from pratfall.models import DecodedOutput, Invocation, ResolvedProfile, ResultError

_ALLOWED = {name: Flag(1) for name in ("--max-tokens", "--enabled-tools", "--disabled-tools")}
_RESERVED = {
    name: Flag(0)
    for name in (
        "--teleport",
        "--continue",
        "-c",
        "--setup",
        "--check-upgrade",
        "--experimental-harness",
        "--legacy-harness",
    )
} | {
    name: Flag(1, joined=name == "-p")
    for name in (
        "--prompt",
        "-p",
        "--output",
        "--max-turns",
        "--max-price",
        "--model",
        "--effort",
        "--agent",
        "--config",
        "--workdir",
        "--worktree",
        "--resume",
        "--remote",
    )
}
_IGNORED = frozenset({"reasoning", "effect", "callback", "checkpoint", "notice"})


def build(resolved: ResolvedProfile, prompt: bytes) -> Invocation:
    arguments = resolved.options.native_args or ()
    argv = [*resolved.command, "--prompt", "--output", "json"]
    if resolved.options.max_turns is not None:
        argv.extend(("--max-turns", str(resolved.options.max_turns)))
    if resolved.options.max_budget_usd is not None:
        argv.extend(("--max-price", str(resolved.options.max_budget_usd)))
    for tool in resolved.options.tools or ():
        argv.append(f"--enabled-tools={tool}")
    for tool in resolved.options.disabled_tools or ():
        argv.append(f"--disabled-tools={tool}")
    if resolved.options.native_agent is not None:
        argv.append(f"--agent={resolved.options.native_agent}")
    return Invocation((*argv, *arguments), prompt)


def validate(resolved: ResolvedProfile) -> None:
    reserved = _RESERVED.copy()
    validate_tool_values(resolved.options.tools, "tools")
    validate_tool_values(resolved.options.disabled_tools, "disabled_tools")
    if resolved.options.tools is not None:
        reserved |= {"--enabled-tools": _ALLOWED["--enabled-tools"]}
    if resolved.options.disabled_tools:
        reserved |= {"--disabled-tools": _ALLOWED["--disabled-tools"]}
    validate_flags("Vibe", resolved.options.native_args or (), _ALLOWED, reserved)


def decode(stdout: str) -> DecodedOutput:
    value = parse(stdout, "Vibe")
    if isinstance(value, ResultError):
        return DecodedOutput(error=value)
    if not isinstance(value, list):
        return DecodedOutput(error=_protocol("Vibe history must be a JSON array."))
    output = ""
    error = None
    for entry in value:
        text = _assistant_text(entry)
        if isinstance(text, ResultError):
            error = error or text
        elif text:
            output = text
    return DecodedOutput(output=output, error=error or document_error(value, "Vibe"))


def _assistant_text(entry: object) -> str | ResultError | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
        return _protocol("Malformed Vibe history entry.")
    if entry["type"] in _IGNORED:
        return None
    if entry["type"] != "message":
        return _protocol("Unknown Vibe history entry type.")
    if entry.get("role") not in ("system", "user", "assistant"):
        return _protocol("Malformed Vibe message role.")
    text = _content_text(entry.get("content"))
    if isinstance(text, ResultError):
        return text
    return text if entry["role"] == "assistant" else None


def _content_text(content: object) -> str | ResultError:
    if not isinstance(content, list):
        return _protocol("Malformed Vibe message content.")
    texts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in ("text", "image", "resource"):
            return _protocol("Malformed Vibe content block.")
        if block["type"] == "text":
            text = block.get("text")
            if not isinstance(text, str):
                return _protocol("Malformed Vibe text block.")
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                return encoding_error()
            texts.append(text)
    return "\n\n".join(texts)


def _protocol(message: str) -> ResultError:
    return ResultError("protocol_error", message)
