"""Serialized operations on one Content repository."""

from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from . import config, core, files, gitio
from . import fleet as fleet_records


ReportT = TypeVar("ReportT", bound=core.Report)
_SERIALIZATION_LOCK = threading.Lock()


class RepositoryBusyError(RuntimeError):
    """Another operation is using the Content repository."""


@contextmanager
def _serialized() -> Iterator[None]:
    if not _SERIALIZATION_LOCK.acquire(blocking=False):
        raise RepositoryBusyError(
            "store is busy; try again after the current operation finishes"
        )
    try:
        yield
    finally:
        _SERIALIZATION_LOCK.release()


class ContentOperations:
    """Load, serialize, and execute operations on one Content repository."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def init(
        self,
        *,
        from_url: str | None = None,
        remote: str | None = None,
        yes: bool = False,
    ) -> core.Report:
        from .store import init_store

        with _serialized():
            return init_store(self.repo, from_url=from_url, remote=remote, yes=yes)

    def migrate(self) -> core.Report:
        from .migration import migrate

        with _serialized():
            return migrate(self.repo)

    def project_link(self, path: Path) -> core.Report:
        from .projects import link_project

        with _serialized():
            return link_project(self.repo, path)

    def status(self, *, fleet: bool = False) -> core.StatusReport:
        return self._report(
            core.StatusReport, lambda projection: _status(projection, fleet)
        )

    def fleet(self) -> dict[str, Any]:
        with _serialized():
            machine_id, _ = config.resolve_machine()
            return {
                "machine_id": machine_id,
                "machines": _fleet(self.repo, machine_id),
            }

    def git(self, *, fetch: bool = True) -> dict[str, Any]:
        with _serialized():
            return gitio.state(self.repo, fetch=fetch)

    def machine_id(self) -> str:
        with _serialized():
            return config.load_machine_projection(self.repo).machine_id

    def state(self) -> dict[str, Any]:
        with _serialized():
            projection = config.load_machine_projection(self.repo)
            return _state(projection)

    def apply(self, *, dry_run: bool = False, copy: bool = False) -> core.ApplyReport:
        return self._report(
            core.ApplyReport,
            lambda projection: core.apply_report(
                config.load_machine_projection(self.repo, copy=True)
                if copy
                else projection,
                dry_run=dry_run,
            ),
            dry_run=dry_run,
            report_os_errors=True,
        )

    def sync(
        self, *, dry_run: bool = False, prefer: str | None = None
    ) -> core.SyncReport:
        return self._report(
            core.SyncReport,
            lambda projection: core.sync_report(
                projection, dry_run=dry_run, prefer=prefer
            ),
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
        project: bool | None = None,
        name: str | None = None,
    ) -> core.AdoptReport:
        return self._report(
            core.AdoptReport,
            lambda projection: core.adopt_skill_report(projection, path, project, name),
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
    projects: list[dict[str, Any]] = [
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
    instruction_paths = [repo / "AGENTS.md"]
    instruction_paths.extend(sorted((repo / "agents").glob("*.md")))
    return {
        "machine_id": projection.machine_id,
        "hostname": projection.hostname,
        "repo": str(repo),
        "agents": agents,
        "projects": projects,
        "skills": {
            "global": _skills(repo / "skills", repo),
            "projects": {
                project.name: _skills(repo / "projects" / project.name / "skills", repo)
                for project in projection.projects
            },
        },
        "instructions": {
            "global": [
                {
                    "name": path.name,
                    "path": _relative(path, repo),
                    "exists": path.is_file(),
                    "kind": "base" if path.name == "AGENTS.md" else "agent",
                }
                for path in instruction_paths
            ],
            "projects": {},
        },
        "config_files": [
            {
                "name": "hub.toml",
                "path": "hub.toml",
                "exists": (repo / "hub.toml").is_file(),
            }
        ],
        "text_suffixes": sorted(files.TEXT_SUFFIXES),
        "max_file_bytes": files.MAX_FILE_BYTES,
    }


@dataclasses.dataclass(frozen=True)
class FleetStatusReport(core.StatusReport):
    fleet: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "fleet": list(self.fleet)}


def _status(
    projection: config.MachineProjection, include_fleet: bool
) -> core.StatusReport:
    report = core.status_report(projection)
    if not include_fleet:
        return report
    machines = _fleet(projection.repo, projection.machine_id)
    checks = list(report.checks)
    local_problem = False
    for machine in machines:
        current = machine["current"]
        behind = machine["behind"]
        state = (
            "current"
            if current
            else f"behind {behind}"
            if behind is not None
            else "unknown"
        )
        seconds = machine["age_seconds"]
        age = f"{int(seconds)}s ago" if seconds is not None else "unknown"
        local = machine["local"]
        problems = machine["problems"]
        bad = (
            not current
            or bool(problems)
            or bool(machine.get("error"))
            or bool(machine.get("status", {}).get("exit_code", 0))
        )
        local_problem |= local and bad
        text = f"{machine['machine']}: {state}; {problems} problems; synced {age}"
        if local:
            text += "; local"
        if machine.get("error"):
            text += f"; {machine['error']}"
        checks.append(
            core.StatusCheck(
                kind="fleet",
                level="DRIFT" if local and bad else "warn" if bad else "ok",
                text=text,
            )
        )
    if not any(machine["local"] for machine in machines):
        local_problem = True
        checks.append(
            core.StatusCheck(
                kind="fleet",
                level="MISSING",
                text=f"{projection.machine_id}: no Machine record; run 'agent-hub sync'",
            )
        )
    return FleetStatusReport(
        machine_id=report.machine_id,
        hostname=report.hostname,
        repo=report.repo,
        checks=tuple(checks),
        exit_code=int(bool(report.exit_code or local_problem)),
        fleet=tuple(machines),
    )


def _fleet(repo: Path, machine_id: str) -> list[dict[str, Any]]:
    try:
        return fleet_records.records(repo, machine_id)
    except (ValueError, gitio.GitError) as exc:
        raise config.ConfigError(f"{repo / 'machines'}: {exc}") from exc
