import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from pratfall.consumer import ConsumerFailure, Retention
from pratfall.errors import PratError
from pratfall.limits import JSON_DEPTH, NUMERIC_BYTES, SCHEMA_BYTES
from pratfall.models import JsonValue, OptionOrigin, PreparedSchema, ResultError
from pratfall.prompt_input import read_bounded_file


def validate_json(value: object, *, max_depth: int = JSON_DEPTH) -> JsonValue:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict | list):
            if depth >= max_depth:
                raise ValueError(f"JSON nesting exceeds {max_depth} containers")
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("JSON object keys must be strings")
                    key.encode("utf-8")
                    pending.append((child, depth + 1))
            else:
                pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int | float):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("JSON contains a nonfinite number")
            if len(str(item)) > NUMERIC_BYTES:
                raise ValueError(f"JSON numeric value exceeds {NUMERIC_BYTES} bytes")
        else:
            raise ValueError("JSON contains an unsupported value")
    return cast(JsonValue, value)


def parse_json(
    text: str, *, max_depth: int = JSON_DEPTH, numeric_bytes: int = NUMERIC_BYTES
) -> JsonValue:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_int=lambda value: _integer(value, numeric_bytes),
            parse_float=lambda value: _number(value, numeric_bytes),
            parse_constant=_constant,
        )
    except RecursionError as error:
        raise ValueError("JSON nesting is excessive") from error
    return validate_json(value, max_depth=max_depth)


def encode_json(value: object) -> str:
    return json.dumps(validate_json(value), ensure_ascii=False, separators=(",", ":"))


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _integer(value: str, limit: int) -> int:
    _numeric_width(value, limit)
    return int(value)


def _number(value: str, limit: int) -> float:
    _numeric_width(value, limit)
    return float(value)


def _numeric_width(value: str, limit: int) -> None:
    if len(value) > limit:
        raise ValueError(f"JSON numeric value exceeds {limit} bytes")


def _constant(value: str) -> JsonValue:
    raise ValueError(f"JSON contains a nonfinite number: {value}")


@contextmanager
def prepare_schema(
    source: str, origin: OptionOrigin, transport: Literal["inline", "file"]
) -> Iterator[PreparedSchema]:
    try:
        data = read_bounded_file(Path(source), limit=SCHEMA_BYTES, label="schema file")
        if len(data) > SCHEMA_BYTES:
            raise ValueError(f"Schema exceeds the {SCHEMA_BYTES} byte limit")
        text = data.decode("utf-8")
        value = parse_json(text)
        if not isinstance(value, dict | bool):
            raise ValueError("a JSON Schema must be an object or boolean")
    except (ValueError, PratError) as error:
        raise PratError(f"{origin.label}: {error}", code=origin.code) from error
    if transport == "inline":
        yield PreparedSchema(text, value)
        return
    try:
        temporary = TemporaryDirectory(prefix="prat-schema-")
    except OSError as error:
        raise PratError(
            f"{origin.label}: cannot prepare schema snapshot: {error}", code=origin.code
        ) from error
    with temporary as directory:
        path = Path(directory) / "schema.json"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        except OSError as error:
            raise PratError(
                f"{origin.label}: cannot prepare schema snapshot: {error}", code=origin.code
            ) from error
        yield PreparedSchema(text, value, str(path))


@dataclass(frozen=True)
class StructuredAnswer:
    value: JsonValue
    output: str


def retain_answer(value: object, retain: Retention, slot: str = "structured") -> StructuredAnswer:
    try:
        typed = validate_json(value)
        output = json.dumps(typed, ensure_ascii=False, separators=(",", ":"))
    except UnicodeEncodeError as error:
        raise ConsumerFailure(
            ResultError("output_encoding", "Structured answer contains invalid Unicode text.")
        ) from error
    except ValueError as error:
        raise ConsumerFailure(ResultError("protocol_error", str(error))) from error
    retain.payload(slot, 2 * len(output.encode("utf-8")), record=True)
    return StructuredAnswer(typed, output)
