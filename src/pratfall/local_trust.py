import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pratfall.errors import PratError
from pratfall.limits import TRUST_STORE_BYTES
from pratfall.prompt_input import read_bounded_file

STORE_VERSION = 2


def digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def store_path(env: Mapping[str, str]) -> Path:
    state = env.get("XDG_STATE_HOME", "")
    home = env.get("HOME")
    try:
        xdg = Path(state).expanduser()
        root = xdg if xdg.is_absolute() else (Path(home) if home else Path.home()) / ".local/state"
    except RuntimeError as error:
        raise PratError(
            f"{state or 'trust store path'}: cannot expand user path ({error}); "
            "set XDG_STATE_HOME to an absolute path."
        ) from error
    return Path(os.path.abspath(root / "pratfall" / "trusted.json"))


def is_trusted(directory: Path, env: Mapping[str, str]) -> bool:
    real = Path(os.path.realpath(directory))
    return any(real.is_relative_to(trusted) for trusted in _trusted_directories(store_path(env)))


def trust(directory: Path, env: Mapping[str, str]) -> Path:
    store = store_path(env)
    directories = {*_trusted_directories(store), Path(os.path.realpath(directory))}
    _write_store(
        store,
        {"version": STORE_VERSION, "directories": sorted(str(path) for path in directories)},
    )
    return store


def _trusted_directories(store: Path) -> tuple[Path, ...]:
    if not store.exists():
        return ()
    raw = read_bounded_file(store, limit=TRUST_STORE_BYTES, label="trust store")
    if len(raw) > TRUST_STORE_BYTES:
        raise PratError(f"{store}: trust store exceeds the {TRUST_STORE_BYTES} byte limit.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PratError(f"{store}: invalid trust store: {error}; delete it to reset.") from error
    if isinstance(value, dict) and value.get("version") == 1:
        return ()
    directories = (
        value.get("directories")
        if isinstance(value, dict) and value.get("version") == STORE_VERSION
        else None
    )
    if not isinstance(directories, list) or not all(
        isinstance(item, str) and Path(item).is_absolute() for item in directories
    ):
        raise PratError(f"{store}: invalid trust store; delete it to reset.")
    return tuple(Path(item) for item in directories)


def _write_store(store: Path, value: dict[str, object]) -> None:
    try:
        missing = [parent for parent in store.parents if not parent.exists()]
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
        descriptor, temporary = tempfile.mkstemp(
            dir=store.parent, prefix=".trusted-", suffix=".json"
        )
    except OSError as error:
        raise PratError(f"{store}: cannot create trust store: {error}.") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, store)
    except OSError as error:
        Path(temporary).unlink(missing_ok=True)
        raise PratError(f"{store}: cannot write trust store: {error}.") from error
