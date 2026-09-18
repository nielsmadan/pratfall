from pathlib import Path

from pratfall.errors import PratError
from pratfall.models import OptionOrigin
from pratfall.prompt_input import read_bounded_file

INSTRUCTIONS_LIMIT = 1024 * 1024


def validate_instructions(value: object, origin: OptionOrigin) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise PratError(
            f"{origin.label}: expected nonempty instructions without NUL bytes.", code=origin.code
        )
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise PratError(
            f"{origin.label}: instructions must be valid UTF-8.", code=origin.code
        ) from error
    if size > INSTRUCTIONS_LIMIT:
        raise PratError(
            f"{origin.label}: instructions exceed the {INSTRUCTIONS_LIMIT} byte limit.",
            code=origin.code,
        )
    return value


def read_instructions(path: str, origin: OptionOrigin) -> str:
    try:
        value = read_bounded_file(Path(path), limit=INSTRUCTIONS_LIMIT, label="instructions file")
        if len(value) > INSTRUCTIONS_LIMIT:
            raise PratError(f"Instructions exceed the {INSTRUCTIONS_LIMIT} byte limit.")
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PratError(
            f"{origin.label}: instructions file must be valid UTF-8.", code=origin.code
        ) from error
    except PratError as error:
        raise PratError(f"{origin.label}: {error}", code=origin.code) from error
    return validate_instructions(text, origin)
