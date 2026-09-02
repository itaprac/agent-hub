"""Serialized operations on one Content repository."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from . import config, core, files


ReportT = TypeVar("ReportT", bound=core.Report)
_SERIALIZATION_LOCK = threading.Lock()


class RepositoryBusyError(RuntimeError):
    """Another operation is using the Content repository."""


@contextmanager
def _serialized() -> Iterator[None]:
    if not _SERIALIZATION_LOCK.acquire(blocking=False):
        raise RepositoryBusyError(
            "repository is busy; try again after the current operation finishes"
        )
    try:
        yield
    finally:
        _SERIALIZATION_LOCK.release()


class ContentOperations:
    """Load, serialize, and execute operations on one Content repository."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def status(self) -> core.StatusReport:
        return self._report(core.StatusReport, core.status_report)

    def machine_id(self) -> str:
        with _serialized():
            return config.load_machine_projection(self.repo).machine_id

    def state(self) -> dict[str, Any]:
        with _serialized():
            projection = config.load_machine_projection(self.repo)
            return _state(projection)

    def apply(self, *, dry_run: bool = False) -> core.ApplyReport:
        return self._report(
            core.ApplyReport,
            lambda projection: core.apply_report(projection, dry_run=dry_run),
            dry_run=dry_run,
            report_os_errors=True,
        )

    def sync(self, *, dry_run: bool = False) -> core.SyncReport:
        return self._report(
            core.SyncReport,
            lambda projection: core.sync_report(projection, dry_run=dry_run),
            dry_run=dry_run,
            report_os_errors=True,
        )

    def add_skill(self, name: str, project: str | None = None) -> core.AddSkillReport:
        return self._report(
            core.AddSkillReport,
            lambda projection: core.add_skill_report(projection, name, project),
            report_os_errors=True,
        )

    def adopt(
        self,
        path: str,
        project: str | None = None,
        name: str | None = None,
    ) -> core.AdoptReport:
        return self._report(
            core.AdoptReport,
            lambda projection: core.adopt_skill_report(
                projection, path, project, name
            ),
            report_os_errors=True,
        )

    def read_file(self, path: Any) -> dict[str, Any]:
        with _serialized():
            return files.read(self.repo, path)

    def write_file(
        self, path: Any, content: Any, revision: str | None
    ) -> dict[str, Any]:
        with _serialized():
            return files.write(self.repo, path, content, revision)

    def delete_file(self, path: Any, revision: str | None) -> dict[str, Any]:
        with _serialized():
            return files.delete(self.repo, path, revision)

    def _report(
        self,
        report_type: type[ReportT],
        operation: Callable[[config.MachineProjection], ReportT],
        *,
        dry_run: bool | None = None,
        report_os_errors: bool = False,
    ) -> ReportT:
        with _serialized():
            try:
                return operation(config.load_machine_projection(self.repo))
            except config.ConfigError as exc:
                return self._error_report(
                    report_type, str(exc), kind="config", exit_code=2, dry_run=dry_run
                )
            except (OSError, UnicodeError) as exc:
                if not report_os_errors:
                    raise
                return self._error_report(
                    report_type, str(exc), kind="error", exit_code=1, dry_run=dry_run
                )

    def _error_report(
        self,
        report_type: type[ReportT],
        message: str,
        *,
        kind: str,
        exit_code: int,
        dry_run: bool | None,
    ) -> ReportT:
        fields: dict[str, Any] = {
            "machine_id": "",
            "hostname": config.machine_name(),
            "repo": str(self.repo),
            "checks": (
                core.StatusCheck(
                    kind=kind,
                    level="ERROR",
                    text=core.one_line(message),
                    target=str(self.repo),
                ),
            ),
            "exit_code": exit_code,
        }
        if dry_run is not None:
            fields["dry_run"] = dry_run
        return cast(ReportT, report_type(**fields))


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _skills(parent: Path, repo: Path) -> list[dict[str, Any]]:
    skills = []
    for child in config.skill_directories(parent):
        child_files = []
        for path in sorted(child.rglob("*"), key=lambda item: str(item).lower()):
            if not path.is_file() or any(
                part.startswith(".") for part in path.relative_to(child).parts
            ):
                continue
            child_files.append(
                {
                    "name": str(path.relative_to(child)),
                    "path": _relative(path, repo),
                    "size": path.stat().st_size,
                    "editable": path.suffix.lower() in files.TEXT_SUFFIXES,
                }
            )
        skills.append(
            {
                "name": child.name,
                "path": _relative(child, repo),
                "files": child_files,
            }
        )
    return skills


def _instructions(
    directory: Path, repo: Path, agents: list[str]
) -> list[dict[str, Any]]:
    names = ["base.md"] + [f"{agent}.md" for agent in agents]
    if directory.is_dir():
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name.lower()):
            if path.name not in names:
                names.append(path.name)
    entries = []
    for name in names:
        path = directory / name
        stem = name[:-3]
        kind = "base" if name == "base.md" else "agent" if stem in agents else "extra"
        entries.append(
            {
                "name": name,
                "path": _relative(path, repo),
                "exists": path.is_file(),
                "kind": kind,
            }
        )
    return entries


def _state(projection: config.MachineProjection) -> dict[str, Any]:
    repo = projection.repo
    agents = [
        {
            "name": agent.name,
            "mode": agent.mode,
            "keys": dict(agent.target_templates),
        }
        for agent in projection.agents
    ]
    projects = [
        {
            "name": project.name,
            "path": str(project.path) if project.path is not None else None,
            "machines": dict(project.machines),
            "available": project.available,
            "note": (
                ""
                if project.available
                else project.reason
                if project.availability == "no_path"
                else "path does not exist on this machine"
            ),
        }
        for project in projection.projects
    ]
    project_names = [project["name"] for project in projects]
    global_agents = [
        agent.name for agent in projection.agents if agent.instructions_global
    ]
    project_agents = [
        agent.name for agent in projection.agents if agent.instructions_project
    ]
    skills_root = repo / "skills"
    instructions_root = repo / "instructions"
    config_dir = repo / "config"
    known_configs = ["hub.toml", "agents.toml", "projects.toml", "skills.toml"]
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.toml"), key=lambda item: item.name.lower()):
            if path.name not in known_configs:
                known_configs.append(path.name)
    return {
        "machine_id": projection.machine_id,
        "hostname": projection.hostname,
        "repo": str(repo),
        "agents": agents,
        "projects": projects,
        "skills": {
            "global": _skills(skills_root / "global", repo),
            "projects": {
                name: _skills(skills_root / "projects" / name, repo)
                for name in project_names
            },
        },
        "instructions": {
            "global": _instructions(
                instructions_root / "global", repo, global_agents
            ),
            "projects": {
                name: _instructions(
                    instructions_root / "projects" / name, repo, project_agents
                )
                for name in project_names
            },
        },
        "config_files": [
            {
                "name": name,
                "path": _relative(config_dir / name, repo),
                "exists": (config_dir / name).is_file(),
            }
            for name in known_configs
        ],
        "text_suffixes": sorted(files.TEXT_SUFFIXES),
        "max_file_bytes": files.MAX_FILE_BYTES,
    }
