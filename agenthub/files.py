"""Safe reads and atomic, revision-checked writes in a Content repository."""

from __future__ import annotations

import hashlib
import stat
import tomllib
from pathlib import Path
from typing import Any

from . import fileio, repository

TEXT_SUFFIXES = frozenset({".md", ".toml", ".txt", ".sh", ".py", ".json", ".yaml", ".yml"})
MAX_FILE_BYTES = 1024 * 1024


class FileError(Exception):
    """A Content file error that an adapter can report to the operator."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def resolve(repo: Path, requested_path: Any) -> Path:
    """Resolve one browser-editable path without allowing traversal or hidden files."""
    if not isinstance(requested_path, str) or not requested_path.strip():
        raise FileError(400, "path is required")
    raw = requested_path.strip()
    if "\x00" in raw:
        raise FileError(400, "path contains an invalid character")
    if raw.startswith("/") or raw.startswith("~"):
        raise FileError(400, "path must be relative to the repository root")
    repo = repo.resolve()
    candidate = (repo / raw).resolve()
    if candidate == repo or repo not in candidate.parents:
        raise FileError(400, f"path escapes the repository root: {raw}")
    path = candidate.relative_to(repo)
    if any(part.startswith(".") for part in path.parts):
        raise FileError(400, f"hidden paths are not editable: {path}")
    editable_content = path.parts[0] in {"skills", "instructions"}
    editable_config = (
        len(path.parts) == 2
        and path.parts[0] == "config"
        and candidate.suffix.lower() == ".toml"
    )
    if not editable_content and not editable_config:
        raise FileError(403, f"path is outside the editable repository areas: {path}")
    if candidate.suffix.lower() not in TEXT_SUFFIXES:
        allowed = " ".join(sorted(TEXT_SUFFIXES))
        raise FileError(400, f"unsupported file type '{candidate.suffix}'; allowed: {allowed}")
    return candidate


def read(repo: Path, requested_path: Any) -> dict[str, Any]:
    """Read one editable UTF-8 file and return its optimistic revision."""
    path = resolve(repo, requested_path)
    if not path.exists():
        raise FileError(404, f"file not found: {relative(path, repo)}")
    if not path.is_file():
        raise FileError(400, f"not a regular file: {relative(path, repo)}")
    try:
        file_stat = path.stat()
        if file_stat.st_size > MAX_FILE_BYTES:
            raise FileError(413, f"file is larger than {MAX_FILE_BYTES} bytes: {file_stat.st_size}")
        data = path.read_bytes()
        content = data.decode("utf-8")
    except UnicodeError as exc:
        raise FileError(400, f"file is not valid UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise FileError(500, f"cannot read file: {exc}") from exc
    if len(data) > MAX_FILE_BYTES:
        raise FileError(413, f"file is larger than {MAX_FILE_BYTES} bytes: {len(data)}")
    return {
        "path": relative(path, repo),
        "content": content,
        "size": len(data),
        "modified": int(file_stat.st_mtime),
        "revision": hashlib.sha256(data).hexdigest(),
    }


def expected_revision(payload: dict[str, Any]) -> str | None:
    """Validate the revision field from a mutation request."""
    if "revision" not in payload:
        raise FileError(428, "revision is required; reload the file before changing it")
    value = payload["revision"]
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise FileError(400, "revision must be a SHA-256 hash or null for a new file")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise FileError(400, "revision must be a SHA-256 hash or null for a new file") from exc
    return value.lower()


def _current_revision(path: Path) -> tuple[str | None, int | None]:
    if not path.exists():
        return None, None
    if not path.is_file():
        raise FileError(400, f"not a regular file: {path}")
    try:
        file_stat = path.stat()
        if file_stat.st_size > MAX_FILE_BYTES:
            raise FileError(413, f"file is larger than {MAX_FILE_BYTES} bytes: {file_stat.st_size}")
        data = path.read_bytes()
        mode = stat.S_IMODE(file_stat.st_mode)
    except OSError as exc:
        raise FileError(500, f"cannot read file: {exc}") from exc
    if len(data) > MAX_FILE_BYTES:
        raise FileError(413, f"file is larger than {MAX_FILE_BYTES} bytes: {len(data)}")
    return hashlib.sha256(data).hexdigest(), mode


def _matching_revision(path: Path, revision: str | None) -> tuple[bool, int]:
    actual, mode = _current_revision(path)
    if actual != revision:
        raise FileError(409, "file changed since it was opened; reload it before saving")
    return actual is not None, mode if mode is not None else 0o644


def _validate_content(repo: Path, path: Path, content: str) -> None:
    relative_path = path.relative_to(repo.resolve())
    if (
        len(relative_path.parts) == 2
        and relative_path.parts[0] == "config"
        and path.suffix.lower() == ".toml"
    ):
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            raise FileError(422, f"invalid TOML: {exc}") from exc


def write(
    repo: Path, requested_path: Any, content: Any, revision: str | None
) -> dict[str, Any]:
    """Atomically write one editable file if its revision still matches."""
    path = resolve(repo, requested_path)
    if not isinstance(content, str):
        raise FileError(400, "content must be a string")
    _validate_content(repo, path, content)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise FileError(413, f"content is larger than {MAX_FILE_BYTES} bytes: {len(encoded)}")
    try:
        with repository.mutation():
            exists, mode = _matching_revision(path, revision)
            path.parent.mkdir(parents=True, exist_ok=True)
            fileio.atomic_write(path, encoded, mode)
    except OSError as exc:
        raise FileError(500, f"cannot write file: {exc}") from exc
    return {
        "path": relative(path, repo),
        "size": len(encoded),
        "created": not exists,
        "revision": hashlib.sha256(encoded).hexdigest(),
    }


def delete(repo: Path, requested_path: Any, revision: str | None) -> dict[str, Any]:
    """Delete one editable file if its revision still matches."""
    path = resolve(repo, requested_path)
    try:
        with repository.mutation():
            if path.is_dir() and not path.is_symlink():
                raise FileError(400, f"refusing to delete a directory: {relative(path, repo)}")
            if not path.exists() and not path.is_symlink():
                raise FileError(404, f"file not found: {relative(path, repo)}")
            _matching_revision(path, revision)
            path.unlink()
    except OSError as exc:
        raise FileError(500, f"cannot delete file: {exc}") from exc
    return {"path": relative(path, repo), "deleted": True}
