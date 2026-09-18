import os
import stat
from pathlib import Path

from pratfall.errors import PratError
from pratfall.models import OptionOrigin


def resolve_path(value: str, base: Path, origin: OptionOrigin) -> str:
    try:
        return str(base / Path(value).expanduser())
    except RuntimeError as error:
        raise PratError(
            f"{origin.label}: cannot expand {value!r}; use an absolute path or a valid ~user path.",
            code=origin.code,
        ) from error


def prepare_directories(paths: tuple[str, ...], origin: OptionOrigin) -> tuple[str, ...]:
    prepared: list[str] = []
    for value in paths:
        try:
            path = Path(value).resolve(strict=True)
            is_directory = path.is_dir()
        except FileNotFoundError as error:
            raise PratError(
                f"{origin.label}: not a directory: {value}", code=origin.code
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise PratError(
                f"{origin.label}: cannot inspect directory {value!r}: {error}.", code=origin.code
            ) from error
        if not is_directory:
            raise PratError(f"{origin.label}: not a directory: {value}", code=origin.code)
        prepared.append(str(path))
    return tuple(prepared)


def prepare_attachments(paths: tuple[str, ...], origin: OptionOrigin) -> tuple[str, ...]:
    prepared: list[str] = []
    for value in paths:
        try:
            path = Path(value).resolve(strict=True)
            if not stat.S_ISREG(path.stat().st_mode):
                raise PratError(
                    f"{origin.label}: not a regular attachment file: {value}", code=origin.code
                )
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise PratError(
                        f"{origin.label}: not a regular attachment file: {value}", code=origin.code
                    )
                os.read(descriptor, 1)
            finally:
                os.close(descriptor)
        except (OSError, RuntimeError, ValueError) as error:
            raise PratError(
                f"{origin.label}: cannot read attachment {value!r}: {error}.", code=origin.code
            ) from error
        prepared.append(str(path))
    return tuple(prepared)
