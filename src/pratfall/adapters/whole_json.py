import json
import math

from pratfall.limits import NUMERIC_BYTES, STDOUT_BYTES
from pratfall.models import ResultError


def parse(stdout: str, agent: str) -> object:
    try:
        size = len(stdout.encode("utf-8"))
    except UnicodeEncodeError:
        return encoding_error()
    if size > STDOUT_BYTES:
        return ResultError("stdout_limit_exceeded", f"{agent} JSON exceeds 8 MiB.")
    try:
        return json.loads(
            stdout, parse_int=_integer, parse_float=_number, object_pairs_hook=_object
        )
    except json.JSONDecodeError as error:
        return _invalid(agent, error.msg)
    except ValueError as error:
        return _invalid(agent, str(error))
    except RecursionError:
        return _invalid(agent, "document nesting is too deep")


def session_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def document_error(value: object, agent: str) -> ResultError | None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return encoding_error()
        elif isinstance(item, float) and not math.isfinite(item):
            return ResultError("protocol_error", f"{agent} JSON contains a nonfinite number.")
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return None


def encodable_text(text: str) -> str:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return text


def encoding_error() -> ResultError:
    return ResultError("output_encoding", "Agent output contains invalid Unicode text.")


def _invalid(agent: str, detail: str) -> ResultError:
    return ResultError("protocol_error", f"Invalid {agent} JSON: {detail}.")


def _integer(value: str) -> int:
    if len(value) > NUMERIC_BYTES:
        raise ValueError("numeric value is too large")
    return int(value)


def _number(value: str) -> float:
    if len(value) > NUMERIC_BYTES:
        raise ValueError("numeric value is too large")
    return float(value)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result
