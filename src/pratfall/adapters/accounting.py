import math
from typing import TypeGuard

from pratfall.models import ResultError


def model_map(value: object, label: str) -> tuple[tuple[str, ...] | None, ResultError | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, _malformed(label)
    models = tuple(value)
    if not all(valid_model(model) for model in models):
        return None, _malformed(label)
    return models or None, None


def model(value: object, label: str) -> tuple[str | None, ResultError | None]:
    if value is None:
        return None, None
    if not valid_model(value):
        return None, _malformed(label)
    return value, None


def cost(value: object, label: str) -> tuple[int | float | None, ResultError | None]:
    if value is None:
        return None, None
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        return None, _malformed(label)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return None, _malformed(label)
    return value, None


def valid_model(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _malformed(label: str) -> ResultError:
    return ResultError("protocol_error", f"{label} is malformed.")
