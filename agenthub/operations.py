"""Serialized operations on one Content repository."""

from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from . import config, core, files, gitio, skills as installed_skills
from . import fleet as fleet_records
from . import remote


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
            machines = _fleet(self.repo, machine_id)
            targets = remote.configured_machines()
            return {
                "machine_id": machine_id,
                "machines": [
                    {**machine, "remote_control": machine["machine"] in targets
                     and not machine["local"]}
                    for machine in machines
                ],
            }

    def remote_run(self, machine: str, command: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Run one configured Machine action, with Store sync around remote Sync."""
        with _serialized():
            local_id, _ = config.resolve_machine()
            if machine == local_id or machine not in remote.configured_machines():
                raise remote.RemoteError("remote Machine is not configured")
            if command not in {"apply", "sync"}:
                raise remote.RemoteError("remote control supports only Apply and Sync")
            checked_target = remote.check(machine)
            before = None
            if command == "sync" and not dry_run:
                before = core.sync_report(config.load_machine_projection(self.repo))
                if before.exit_code:
                    result = before.to_dict()
                    result["target_machine"] = machine
                    result["remote_started"] = False
                    result["lines"].append({"level": "ERROR", "text":
                        f"Sync on {machine} was not started: Sync on {local_id} failed"})
                    return result
            result = remote.run(machine, command, dry_run=dry_run, checked_target=checked_target)
            result["target_machine"] = machine
            result["remote_started"] = True
            result["remote_exit_code"] = result["exit_code"]
            result["lines"] = [
                {**line, "text": f"[{machine}] {line['text']}"}
                for line in result["lines"]
            ]
            if before is not None:
                result["lines"][:0] = [
                    {**line, "text": f"[{local_id}, before] {line['text']}"}
                    for line in before.lines()
                ]
            if command == "sync" and not dry_run and result["exit_code"] == 0:
                after = core.sync_report(config.load_machine_projection(self.repo))
                result["refresh_exit_code"] = after.exit_code
                result["lines"].extend(
                    {**line, "text": f"[{local_id}, after] {line['text']}"}
                    for line in after.lines()
                )
                if after.exit_code:
                    result["exit_code"] = after.exit_code
                    result["lines"].append({"level": "ERROR", "text":
                        f"Sync on {machine} completed, but refreshing {local_id} failed"})
            return result

    def git(self, *, fetch: bool = True) -> dict[str, Any]:
        with _serialized():
            return gitio.state(self.repo, fetch=fetch)

    def state(self) -> dict[str, Any]:
        with _serialized():
            projection = config.load_machine_projection(self.repo)
            return _state(projection)

    def apply(self, *, dry_run: bool = False, copy: bool = False) -> core.ApplyReport:
        return self._report(
            core.ApplyReport,
            lambda projection: core.apply_report(
                projection,
                dry_run=dry_run,
            ),
            copy=copy,
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

    def install(self, source: str, skill: str | None = None) -> installed_skills.InstallReport:
        with _serialized():
            return installed_skills.install(self.repo, source, skill)

    def update(self, names: list[str] | None = None) -> installed_skills.InstallReport:
        with _serialized():
            return installed_skills.update(self.repo, names)

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
        copy: bool = False,
    ) -> ReportT:
        with _serialized():
            try:
                return operation(config.load_machine_projection(self.repo, copy=copy))
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


def _skills(
    parent: Path, repo: Path,
    provenance: dict[str, dict[str, str | None]] | None = None,
) -> list[dict[str, Any]]:
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
                "installed": child.name in (provenance or {}),
                "provenance": (provenance or {}).get(child.name),
            }
        )
    return skills


def _state(projection: config.MachineProjection) -> dict[str, Any]:
    repo = projection.repo
    settings = projection.settings
    enabled = {agent.name for agent in projection.agents}
    agents = [
        {
            "name": agent.id,
            "display_name": agent.name,
            "detected": agent.detected,
            "enabled": agent.id in enabled,
            "universal": agent.universal,
            "mode": settings["mode"],
            "keys": {
                key: str(value)
                for key, value in (
                    ("skills_global", agent.skills_global),
                    ("skills_project", agent.skills_project),
                    ("instructions_global", agent.instructions_global),
                ) if value is not None
            },
        }
        for agent in sorted(settings["agents"].values(), key=lambda item: item.id)
    ]
    warnings = []
    try:
        provenance = installed_skills.read_provenance(repo)
    except ValueError as exc:
        provenance = {}
        warnings.append(str(exc))
    projects: list[dict[str, Any]] = [
        {
            "name": project.name,
            "path": str(project.path),
            "available": project.available,
            "note": project.reason,
        }
        for project in projection.projects
    ]
    instruction_paths = [repo / "AGENTS.md"]
    instruction_paths.extend(sorted((repo / "agents").glob("*.md")))
    return {
        "machine_id": projection.machine_id,
        "hostname": projection.hostname,
        "repo": str(repo),
        "store": str(repo),
        "hub_config_exists": (repo / "hub.toml").is_file(),
        "warnings": warnings,
        "agents": agents,
        "projects": projects,
        "skills": {
            "global": _skills(repo / "skills", repo, provenance),
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
