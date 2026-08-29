"""Durable local writes and restricted secret-file reads."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class SecretFileError(Exception):
    """A secret file is unsafe or unreadable."""

    def __init__(self, kind: str, path: Path, detail: str = "") -> None:
        super().__init__(detail)
        self.kind = kind
        self.path = path
        self.detail = detail


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace one file durably while preserving the requested mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_secret(path: Path, *, require_value: bool = False) -> str:
    """Read a UTF-8 secret only from a regular file with mode 600."""
    if not path.exists():
        return ""
    if not path.is_file():
        raise SecretFileError("not_file", path)
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise SecretFileError("permissions", path)
        value = path.read_text(encoding="utf-8").strip()
    except SecretFileError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SecretFileError("read", path, str(exc)) from exc
    if require_value and not value:
        raise SecretFileError("empty", path)
    return value
