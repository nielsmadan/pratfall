from string import Template

from pratfall.errors import PratError
from pratfall.prompt_input import PROMPT_LIMIT


def validate_template(prompt: str, label: str) -> None:
    template = Template(prompt)
    if not template.is_valid() or set(template.get_identifiers()) - {"input"}:
        raise PratError(f"{label}: only $input, ${{input}}, and $$ placeholders are supported.")
    if len(prompt.encode("utf-8")) > PROMPT_LIMIT:
        raise PratError(f"{label}: template exceeds the {PROMPT_LIMIT} byte limit.")


def render_template(prompt: str, base: bytes) -> bytes:
    template = Template(prompt)
    size = len(prompt.encode("utf-8"))
    interpolated = False
    for match in template.pattern.finditer(prompt):
        if match.group("escaped") is not None:
            size -= 1
        else:
            interpolated = True
            size += len(base) - len(match.group().encode("utf-8"))
    if not interpolated and base:
        size += 2 + len(base)
    if size > PROMPT_LIMIT:
        raise PratError(f"Prompt exceeds the {PROMPT_LIMIT} byte limit.", code="invalid_arguments")
    rendered = template.substitute(input=base.decode("utf-8")).encode("utf-8")
    if not interpolated and base:
        rendered += b"\n\n" + base
    if not rendered.decode("utf-8").strip():
        raise PratError("Prompt must not be empty or whitespace-only.", code="invalid_arguments")
    return rendered
