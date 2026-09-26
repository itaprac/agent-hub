"""Disable, enable, and delete Skills and instructions; keep instruction backups.

A disabled item moves to the same relative path under `disabled/` in the Store,
so Apply stops deploying it and Sync carries the change to every Machine.
A backup is a copy of one editable file under `backups/<path>/`.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, core, fileio, files

DISABLED = "disabled"
BACKUPS = "backups"
LOCKFILE = ".skill-lock.json"
SKILL_ACTIONS = frozenset({"disable", "enable", "delete"})
INSTRUCTION_ACTIONS = frozenset({"disable", "enable"})
BACKUP_ACTIONS = frozenset({"create", "restore", "delete"})
BACKUP_NAME = re.compile(r"^\d{8}-\d{6}(?:-\d+)?(?:--[a-z0-9-]{1,40})?$")


class LifecycleError(ValueError):
    """A request that names an unknown or conflicting item."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclasses.dataclass(frozen=True)
class LifecycleReport(core.Report):
    operation: str = "skill"

    @property
    def command(self) -> str:
        return self.operation


def _plain_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LifecycleError(400, f"{label} is required")
    value = value.strip()
    if (
        value.startswith(".")
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise LifecycleError(400, f"invalid {label}: {value!r}")
    return value


def _skill_parents(repo: Path, project: str | None) -> tuple[Path, Path]:
    """The enabled and disabled parent directories for one Skill scope."""
    relative = Path("skills") if project is None else Path("projects", project, "skills")
    return repo / relative, repo / DISABLED / relative


def _checked(repo: Path, path: Path) -> Path:
    """Reject a path whose parents are symlinks or leave the Store."""
    try:
        parts = path.relative_to(repo).parts
    except ValueError as exc:
        raise LifecycleError(400, f"path escapes the Store: {path}") from exc
    current = repo
    for part in parts[:-1]:
        current /= part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise LifecycleError(400, f"{current}: parent must be a regular directory")
    return path


def _move(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise LifecycleError(
            409, f"{destination} already exists; rename or delete it first"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def _prune_empty(repo: Path, directory: Path) -> None:
    """Remove empty parents left under one Store area after an item moves out."""
    for area in (repo / DISABLED, repo / BACKUPS):
        while (
            (directory == area or directory.is_relative_to(area))
            and directory.is_dir()
            and not directory.is_symlink()
            and not any(directory.iterdir())
        ):
            directory.rmdir()
            if directory == area:
                return
            directory = directory.parent


# --------------------------------------------------------------------- lockfile


def _read_lock(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        raise ValueError(f"{path}: not a skills.sh lockfile")
    return data


def _write_lock(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fileio.atomic_write(path, text.encode("utf-8"), 0o644)


def _move_provenance(source: Path, destination: Path | None, name: str) -> str | None:
    """Move one skills.sh lock entry between lockfiles, or drop it."""
    try:
        data = _read_lock(source)
        if data is None or name not in data["skills"]:
            return None
        entry = data["skills"].pop(name)
        if destination is not None:
            target = _read_lock(destination) or {
                "version": data.get("version", 3),
                "skills": {},
            }
            target["skills"][name] = entry
            _write_lock(destination, target)
        if not data["skills"] and source.parent.name == DISABLED:
            source.unlink()
        else:
            _write_lock(source, data)
    except (OSError, UnicodeError, ValueError) as exc:
        return f"skills.sh provenance for {name} was not moved: {exc}"
    return None


# --------------------------------------------------------------------- reports


def _report(
    projection: config.MachineProjection,
    operation: str,
    checks: list[core.StatusCheck],
    *,
    apply: bool = True,
) -> LifecycleReport:
    if apply:
        applied = core.apply_report(config.load_machine_projection(projection.repo))
        checks.extend(applied.checks)
    problems = sum(1 for check in checks if check.level in core.PROBLEM_LEVELS)
    return LifecycleReport(
        machine_id=projection.machine_id,
        hostname=projection.hostname,
        repo=str(projection.repo),
        checks=tuple(checks),
        exit_code=1 if problems else 0,
        operation=operation,
    )


def _check(kind: str, level: str, text: str, **fields: Any) -> core.StatusCheck:
    return core.StatusCheck(kind=kind, level=level, text=text, **fields)


# ----------------------------------------------------------------------- skills


def skill(
    projection: config.MachineProjection,
    action: str,
    name: Any,
    project: Any = None,
) -> LifecycleReport:
    """Disable, enable, or delete one global or Project skill, then apply."""
    if action not in SKILL_ACTIONS:
        raise LifecycleError(400, f"action must be one of: {', '.join(sorted(SKILL_ACTIONS))}")
    repo = projection.repo
    name = _plain_name(name, "skill name")
    slug = None if project in (None, "") else _plain_name(project, "project")
    enabled_parent, disabled_parent = _skill_parents(repo, slug)
    enabled = _checked(repo, enabled_parent / name)
    disabled = _checked(repo, disabled_parent / name)
    fields = {"name": name, "project": slug}
    lock = repo / LOCKFILE
    disabled_lock = repo / DISABLED / LOCKFILE
    checks: list[core.StatusCheck] = []

    def real_directory(path: Path) -> bool:
        return path.is_dir() and not path.is_symlink()

    if action == "disable":
        if not real_directory(enabled):
            raise LifecycleError(404, f"enabled Skill not found: {files.relative(enabled, repo)}")
        _move(enabled, disabled)
        checks.append(_check("skill", "ok", f"disabled {name}; moved to {files.relative(disabled, repo)}", target=str(disabled), **fields))
        if slug is None:
            warning = _move_provenance(lock, disabled_lock, name)
            if warning:
                checks.append(_check("skill", "warn", warning, **fields))
    elif action == "enable":
        if not real_directory(disabled):
            raise LifecycleError(404, f"disabled Skill not found: {files.relative(disabled, repo)}")
        _move(disabled, enabled)
        _prune_empty(repo, disabled.parent)
        checks.append(_check("skill", "ok", f"enabled {name}; moved to {files.relative(enabled, repo)}", target=str(enabled), **fields))
        if slug is None:
            warning = _move_provenance(disabled_lock, lock, name)
            if warning:
                checks.append(_check("skill", "warn", warning, **fields))
            _prune_empty(repo, disabled_lock.parent)
    else:
        source = enabled if real_directory(enabled) else disabled
        if not real_directory(source):
            raise LifecycleError(404, f"Skill not found: {name}")
        shutil.rmtree(source)
        _prune_empty(repo, source.parent)
        checks.append(_check("skill", "ok", f"deleted {files.relative(source, repo)}; Git history keeps committed versions", target=str(source), **fields))
        if slug is None:
            for path in (lock, disabled_lock):
                warning = _move_provenance(path, None, name)
                if warning:
                    checks.append(_check("skill", "warn", warning, **fields))
            _prune_empty(repo, disabled_lock.parent)
    return _report(projection, f"{action} skill", checks)


# ----------------------------------------------------------------- instructions


def _instruction_path(value: Any) -> Path:
    """Map an enabled or disabled instruction path to its enabled relative path."""
    if not isinstance(value, str) or not value.strip():
        raise LifecycleError(400, "path is required")
    path = Path(value.strip())
    if path.parts and path.parts[0] == DISABLED:
        path = Path(*path.parts[1:])
    if path.as_posix() == "AGENTS.md":
        return path
    if len(path.parts) == 2 and path.parts[0] == "agents" and path.suffix == ".md":
        _plain_name(path.name, "Overlay")
        return path
    raise LifecycleError(400, "path must be AGENTS.md or agents/<agent-id>.md")


def instruction(
    projection: config.MachineProjection, action: str, path: Any
) -> LifecycleReport:
    """Disable or enable `AGENTS.md` or one Overlay, then apply."""
    if action not in INSTRUCTION_ACTIONS:
        raise LifecycleError(400, f"action must be one of: {', '.join(sorted(INSTRUCTION_ACTIONS))}")
    repo = projection.repo
    relative = _instruction_path(path)
    enabled = _checked(repo, repo / relative)
    disabled = _checked(repo, repo / DISABLED / relative)
    source, destination = (enabled, disabled) if action == "disable" else (disabled, enabled)
    if not source.is_file() or source.is_symlink():
        raise LifecycleError(404, f"file not found: {files.relative(source, repo)}")
    _move(source, destination)
    _prune_empty(repo, source.parent)
    checks = [
        _check(
            "instruction",
            "ok",
            f"{action}d {relative.as_posix()}; moved to {files.relative(destination, repo)}",
            target=str(destination),
        )
    ]
    return _report(projection, f"{action} instruction", checks)


# ---------------------------------------------------------------------- backups


def _backup_root(repo: Path, path: str) -> Path:
    return repo / BACKUPS / path


def _label(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LifecycleError(400, "label must be a string")
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:40].strip("-")


def backups(repo: Path) -> dict[str, list[dict[str, Any]]]:
    """Every backup, keyed by the Store path of the file it copies."""
    root = repo / BACKUPS
    result: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir() or root.is_symlink():
        return result
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.stem.startswith("."):
            continue
        if BACKUP_NAME.fullmatch(path.stem) is None:
            continue
        source = path.parent.relative_to(root).as_posix()
        stamp, _, label = path.stem.partition("--")
        try:
            created = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").isoformat()
        except ValueError:
            continue
        result.setdefault(source, []).append(
            {
                "id": path.name,
                "path": files.relative(path, repo),
                "created": created,
                "label": label.replace("-", " "),
                "size": path.stat().st_size,
            }
        )
    for entries in result.values():
        entries.sort(key=lambda entry: entry["id"], reverse=True)
    return result


def _backup_file(repo: Path, source: str, backup_id: Any) -> Path:
    if (
        not isinstance(backup_id, str)
        or Path(backup_id).name != backup_id
        or BACKUP_NAME.fullmatch(Path(backup_id).stem) is None
    ):
        raise LifecycleError(400, "id must name one backup")
    path = _checked(repo, _backup_root(repo, source) / backup_id)
    if not path.is_file() or path.is_symlink():
        raise LifecycleError(404, f"backup not found: {backup_id}")
    return path


def backup(
    projection: config.MachineProjection,
    action: str,
    path: Any,
    *,
    backup_id: Any = None,
    label: Any = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Create, restore, or delete one backup of an editable Store file."""
    if action not in BACKUP_ACTIONS:
        raise LifecycleError(400, f"action must be one of: {', '.join(sorted(BACKUP_ACTIONS))}")
    repo = projection.repo
    try:
        source = files.resolve(repo, path)
    except files.FileError as exc:
        raise LifecycleError(exc.status, exc.message) from exc
    relative = files.relative(source, repo)
    if relative.split("/")[0] == BACKUPS:
        raise LifecycleError(400, "a backup cannot have its own backups")
    # Backups follow a file while it is disabled, so key them by its enabled path.
    key = relative.removeprefix(f"{DISABLED}/")
    if action == "create":
        current = files.read(repo, relative)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"--{slug}" if (slug := _label(label)) else ""
        directory = _checked(repo, _backup_root(repo, key) / "placeholder").parent
        target = directory / f"{stamp}{suffix}{source.suffix}"
        counter = 2
        while target.exists():
            target = directory / f"{stamp}-{counter}{suffix}{source.suffix}"
            counter += 1
        fileio.atomic_write(target, current["content"].encode("utf-8"), 0o644)
        return {"action": action, "path": relative, "backup": files.relative(target, repo)}
    stored = _backup_file(repo, key, backup_id)
    if action == "delete":
        stored.unlink()
        _prune_empty(repo, stored.parent)
        return {"action": action, "path": relative, "backup": files.relative(stored, repo)}
    content = stored.read_text(encoding="utf-8")
    written = files.write(repo, relative, content, revision)
    report = core.apply_report(config.load_machine_projection(repo))
    return {
        "action": action,
        "path": relative,
        "backup": files.relative(stored, repo),
        "revision": written["revision"],
        "apply": report.to_dict(),
    }

