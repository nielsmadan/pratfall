import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pratfall.errors import PratError
from pratfall.limits import TRUST_STORE_BYTES
from pratfall.prompt_input import read_bounded_file

TrustState = Literal["trusted", "changed", "untrusted"]


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


def trust_state(path: Path, contents: bytes, env: Mapping[str, str]) -> TrustState:
    recorded = _trusted_files(store_path(env)).get(str(path))
    if recorded is None:
        return "untrusted"
    return "trusted" if recorded == digest(contents) else "changed"


def trust(path: Path, contents: bytes, env: Mapping[str, str]) -> Path:
    store = store_path(env)
    files = _trusted_files(store) | {str(path): digest(contents)}
    _write_store(store, {"version": 1, "files": dict(sorted(files.items()))})
    return store


def _trusted_files(store: Path) -> dict[str, str]:
    if not store.exists():
        return {}
    raw = read_bounded_file(store, limit=TRUST_STORE_BYTES, label="trust store")
    if len(raw) > TRUST_STORE_BYTES:
        raise PratError(f"{store}: trust store exceeds the {TRUST_STORE_BYTES} byte limit.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PratError(f"{store}: invalid trust store: {error}; delete it to reset.") from error
    files = value.get("files") if isinstance(value, dict) and value.get("version") == 1 else None
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in files.items()
    ):
        raise PratError(f"{store}: invalid trust store; delete it to reset.")
    return files


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
