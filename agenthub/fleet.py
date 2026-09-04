"""Machine records and Fleet freshness, stored in Git with the Skills."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from . import __version__, gitio

CONTENT_PATHS = ("--", ".", ":(exclude)machines")
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def utc_now() -> datetime:
    """Clock seam for record age and heartbeat tests."""
    return datetime.now(timezone.utc)


def _git(repo: Path, *args: str) -> str:
    result = gitio.run_git(repo, *args)
    if result.returncode:
        raise gitio.GitCommandError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _valid_head(repo: Path, head: Any) -> bool:
    return (
        isinstance(head, str)
        and _SHA.fullmatch(head) is not None
        and gitio.run_git(repo, "cat-file", "-e", f"{head}^{{commit}}").returncode == 0
    )


def _ancestor(repo: Path, older: str, newer: str) -> bool:
    result = gitio.run_git(repo, "merge-base", "--is-ancestor", older, newer)
    if result.returncode not in (0, 1):
        raise gitio.GitCommandError(result.stderr.strip())
    return result.returncode == 0


def _age(value: Any, now: datetime) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
        if when.tzinfo is None:
            return None
        return max(0.0, (now - when).total_seconds())
    except ValueError:
        return None


def _read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"{path}: Machine record must not be a symlink")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path}: Machine record must be an object")
    return record


def write_record(
    repo: Path,
    *,
    machine_id: str,
    hostname: str,
    agents: list[str],
    head: str,
    exit_code: int,
    problems: int,
) -> bool:
    """Write a changed record, or a heartbeat after 24 hours. Return whether written."""
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", machine_id):
        raise ValueError("invalid Machine ID")
    directory = repo / "machines"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError(f"{directory}: expected a regular Machine record directory")
    path = directory / f"{machine_id}.json"
    if path.is_symlink():
        raise ValueError(f"{path}: Machine record must not be a symlink")
    if not _valid_head(repo, head):
        raise ValueError(f"cannot record unknown applied commit {head}")
    previous: dict[str, Any] = {}
    if path.exists():
        try:
            previous = _read_record(path)
        except (ValueError, UnicodeError):
            previous = {}
    old_head = previous.get("head")
    if (
        isinstance(old_head, str)
        and _valid_head(repo, old_head)
        and _ancestor(repo, old_head, head)
    ):
        changes = _git(
            repo, "rev-list", "--count", f"{old_head}..{head}", *CONTENT_PATHS
        )
        if changes == "0":
            head = old_head
    now = utc_now()
    record = {
        "machine": machine_id,
        "hostname": hostname,
        "os": sys.platform,
        "app": __version__,
        "agents": sorted(set(agents)),
        "head": head,
        "status": {"exit_code": exit_code, "problems": problems},
        "synced_at": now.isoformat(timespec="seconds"),
    }
    age = _age(previous.get("synced_at"), now)
    old_fields = {key: value for key, value in previous.items() if key != "synced_at"}
    new_fields = {key: value for key, value in record.items() if key != "synced_at"}
    if old_fields == new_fields and age is not None and age <= 24 * 60 * 60:
        return False
    directory.mkdir(exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{machine_id}-", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def records(repo: Path, machine_id: str) -> list[dict[str, Any]]:
    """List committed-history freshness and record ages without contacting Machines."""
    directory = repo / "machines"
    if directory.is_symlink():
        raise ValueError(
            f"{directory}: Machine records directory must not be a symlink"
        )
    if not directory.exists():
        return []
    latest = _git(repo, "log", "-1", "--format=%H", *CONTENT_PATHS)
    now = utc_now()
    result = []
    for path in sorted(directory.glob("*.json")):
        row: dict[str, Any] = {
            "machine": path.stem,
            "current": False,
            "behind": None,
            "problems": 0,
            "synced_at": None,
            "age_seconds": None,
            "local": path.stem == machine_id,
        }
        try:
            record = _read_record(path)
            if record.get("machine") != path.stem:
                raise ValueError(f"{path}: Machine ID must match the filename")
            row.update(record)
            row.pop("error", None)
            # Computed fields cannot be overridden by a record from another Machine.
            row.update(
                current=False,
                behind=None,
                local=path.stem == machine_id,
                problems=0,
                age_seconds=None,
            )
            status = record.get("status")
            if (
                not isinstance(status, dict)
                or type(status.get("problems")) is not int
                or status["problems"] < 0
            ):
                raise ValueError(
                    f"{path}: status.problems must be a non-negative integer"
                )
            row["problems"] = status["problems"]
            row["age_seconds"] = _age(record.get("synced_at"), now)
            if row["age_seconds"] is None:
                raise ValueError(
                    f"{path}: synced_at must be a timestamp with a time zone"
                )
            head = record.get("head")
            if not isinstance(head, str) or not _valid_head(repo, head):
                raise ValueError(f"{path}: applied commit is not available locally")
            row["current"] = not latest or _ancestor(repo, latest, head)
            row["behind"] = (
                0
                if row["current"]
                else int(
                    _git(repo, "rev-list", "--count", f"{head}..HEAD", *CONTENT_PATHS)
                )
            )
        except (ValueError, OSError, gitio.GitError) as exc:
            row.update(
                current=False,
                behind=None,
                problems=max(1, row.get("problems", 0)),
                error=str(exc),
            )
        result.append(row)
    return result
