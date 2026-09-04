"""Local Project registration and private Skill links in Git checkouts."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import config, core, fileio, gitio

_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclasses.dataclass(frozen=True)
class ProjectLinkReport(core.Report):
    @property
    def command(self) -> str:
        return "project link"


def _git(checkout: Path, *args: str) -> str:
    result = gitio.run_git(checkout, *args)
    if result.returncode:
        raise gitio.GitCommandError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def slug_from_url(url: str) -> str:
    """Use the same slug for HTTPS, SSH URLs, and scp-style origins."""
    value = url.strip()
    if not value or any(character in value for character in "\r\n\x00"):
        raise ValueError("project origin must be a non-empty URL or Git path")
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError("project origin must not contain a query or fragment")
        host = parsed.hostname or ""
        port = parsed.port
        if port is not None and (parsed.scheme.lower(), port) not in {
            ("ssh", 22), ("https", 443), ("http", 80),
        }:
            host += f"-{port}"
        value = f"{host}/{parsed.path.lstrip('/')}"
    elif not value.startswith(("/", "./", "../")) and ":" in value:
        host, path = value.split(":", 1)
        value = host.rsplit("@", 1)[-1] + "/" + path.lstrip("/")
    value = value.strip("/").lower()
    if value.endswith(".git"):
        value = value[:-4]
    components = value.split("/")
    if any(not part or part in {".", ".."} for part in components):
        raise ValueError("project origin must have a stable repository path")
    slug = "--".join(components)
    if _SLUG.fullmatch(slug) is None:
        raise ValueError("project origin contains unsupported characters")
    return slug


def project_root(path: Path) -> Path:
    """Find the containing checkout, including linked Git worktrees."""
    directory = Path(path).expanduser().resolve()
    if directory.is_file():
        directory = directory.parent
    if not directory.is_dir():
        raise ValueError(f"project directory not found: {directory}")
    return Path(_git(directory, "rev-parse", "--show-toplevel")).resolve()


def project_slug(path: Path) -> str:
    checkout = project_root(path)
    result = gitio.run_git(checkout, "remote", "get-url", "origin")
    if result.returncode or not result.stdout.strip():
        raise ValueError(f"{checkout}: project has no origin URL")
    return slug_from_url(result.stdout.strip())


def _safe_descendant(root: Path, path: Path) -> None:
    """Reject parent symlinks and paths that can redirect writes outside root."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{path}: path is outside {root}") from exc
    if ".." in relative.parts:
        raise ValueError(f"{path}: parent traversal is not allowed")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError(f"{current}: parent must be a regular directory")


def _registry_path() -> Path:
    home = Path.home().resolve()
    path = home / ".config" / "agent-hub" / "projects.json"
    _safe_descendant(home, path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{path}: Project registry must be a regular file")
    return path


def load_projects() -> dict[str, Path]:
    """Read only local absolute checkout paths; the registry never enters Git."""
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"{path}: cannot read Project registry: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path}: Project registry must map slugs to absolute paths")
    result = {}
    for slug, value in document.items():
        if _SLUG.fullmatch(slug) is None or not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{path}: invalid Project registry entry {slug!r}")
        checkout = Path(value)
        if not checkout.is_absolute() or ".." in checkout.parts:
            raise ValueError(f"{path}: Project {slug!r} must have an absolute path")
        result[slug] = checkout
    return result


def _save_project(slug: str, checkout: Path) -> bool:
    registry = load_projects()
    if registry.get(slug) == checkout:
        return False
    registry[slug] = checkout
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({name: str(value) for name, value in sorted(registry.items())}, indent=2)
    fileio.atomic_write(path, (data + "\n").encode(), 0o600)
    return True


def _exclude_file(checkout: Path) -> Path:
    common = Path(_git(checkout, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = checkout / common
    # Git can place linked-worktree metadata outside the checkout. Trust only
    # its reported common directory, and reject symlinks within that directory.
    if common.is_symlink():
        raise ValueError(f"{common}: Git directory must not be a symlink")
    common = common.resolve()
    path = common / "info" / "exclude"
    _safe_descendant(common, path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{path}: Git exclude must be a regular file")
    return path


def _exclude_pattern(checkout: Path, path: Path) -> str:
    _safe_descendant(checkout, path)
    relative = path.relative_to(checkout).as_posix()
    if any(character in relative for character in "\r\n\x00"):
        raise ValueError("Project skill paths must not contain line breaks")
    escaped = re.sub(r"([\\*?\[\]#! ])", r"\\\1", relative)
    return "/" + escaped


def exclude_paths(checkout: Path, paths: list[Path], dry_run: bool = False) -> list[core.StatusCheck]:
    """Exclude exact link paths without changing any committed Project files."""
    checkout = project_root(checkout)
    destination = _exclude_file(checkout)
    existing = destination.read_bytes() if destination.exists() else b""
    existing_lines = set(existing.splitlines())
    patterns = sorted({_exclude_pattern(checkout, path) for path in paths})
    additions = [pattern for pattern in patterns if pattern.encode("utf-8") not in existing_lines]
    if not additions:
        return []
    if not dry_run:
        separator = b"\n" if existing and not existing.endswith(b"\n") else b""
        destination.parent.mkdir(parents=True, exist_ok=True)
        fileio.atomic_write(destination, existing + separator + ("\n".join(additions) + "\n").encode(), 0o644)
    return [core.StatusCheck(
        kind="project", level="ok", target=str(destination),
        text=f"{'would exclude' if dry_run else 'excluded'} {len(additions)} Project skill path(s) in {destination}",
    )]


def _tracked(checkout: Path, target: Path) -> bool:
    return bool(_git(checkout, "ls-files", "--", target.relative_to(checkout).as_posix()))


def _directories(projection: config.MachineProjection, checkout: Path) -> dict[Path, set[str]]:
    result: dict[Path, set[str]] = {checkout / ".agents" / "skills": set()}
    for agent in projection.agents:
        if not agent.universal and agent.skills_project:
            result.setdefault(checkout / agent.skills_project, set()).add(agent.name)
    return result


def _project_checks(projection: config.MachineProjection, *, apply: bool, dry_run: bool = False) -> list[core.StatusCheck]:
    settings = config.load_settings(projection.repo)
    checks = []
    selected_agents = {agent.name for agent in projection.agents}
    for project in projection.projects:
        if not project.available or project.path is None:
            continue
        checkout = project.path
        try:
            if checkout.is_symlink() or project_root(checkout) != checkout:
                raise ValueError(f"{checkout}: recorded Project path must be a checkout root")
            if project_slug(checkout) != project.name:
                raise ValueError(f"{checkout}: origin no longer matches recorded Project {project.name}")
            _exclude_file(checkout)
            source_root = projection.repo / "projects" / project.name / "skills"
            _safe_descendant(projection.repo, source_root / "placeholder")
            sources = config.skill_directories(source_root)
            for directory, agent_ids in _directories(projection, checkout).items():
                try:
                    _safe_descendant(checkout, directory / "placeholder")
                except ValueError as exc:
                    checks.append(core.StatusCheck(kind="project", level="DRIFT", text=str(exc), project=project.name, target=str(directory)))
                    continue
                expected = set()
                expected_sources = set()
                deployed = []
                for source in sources:
                    filters = settings["skills"].get(source.name, {})
                    if "machines" in filters and projection.machine_id not in filters["machines"]:
                        continue
                    allowed_agents = selected_agents if directory == checkout / ".agents" / "skills" else agent_ids
                    if "agents" in filters and not allowed_agents.intersection(filters["agents"]):
                        continue
                    expected.add(source.name)
                    expected_sources.add(source.resolve())
                    target = directory / source.name
                    if _tracked(checkout, target):
                        checks.append(core.StatusCheck(kind="skill", level="DRIFT", text=f"{target}: tracked Project path; leave it unchanged", project=project.name, target=str(target)))
                        continue
                    item = config.SkillTarget(
                        sorted(agent_ids)[0] if agent_ids else "universal", project.name,
                        source.name, source, target, "symlink",
                    )
                    check = core.apply_symlink(item, dry_run, projection.repo) if apply else core.check_skill(item)
                    checks.append(check)
                    if check.level not in core.PROBLEM_LEVELS:
                        deployed.append(target)
                if directory.is_dir():
                    for entry in sorted(directory.iterdir()):
                        if entry.name in expected or not entry.is_symlink():
                            continue
                        try:
                            destination = entry.resolve(strict=False)
                            if destination in expected_sources:
                                continue
                            owned = destination.is_relative_to(source_root.resolve())
                        except (OSError, RuntimeError):
                            owned = False
                        if not owned:
                            continue
                        if _tracked(checkout, entry):
                            checks.append(core.StatusCheck(kind="skill", level="DRIFT", text=f"{entry}: tracked Project path; leave it unchanged", project=project.name, target=str(entry)))
                            continue
                        if apply and not dry_run:
                            entry.unlink()
                        checks.append(core.StatusCheck(
                            kind="prune" if apply else "orphan", level="prune" if apply else "STALE",
                            text=f"{'would remove' if dry_run else 'remove' if apply else 'orphaned Project skill link'} {entry}",
                            project=project.name, target=str(entry),
                        ))
                if apply:
                    checks.extend(exclude_paths(checkout, deployed, dry_run))
        except (ValueError, OSError, RuntimeError) as exc:
            checks.append(core.StatusCheck(kind="project", level="DRIFT", text=core.one_line(str(exc)), project=project.name, target=str(checkout)))
    return checks


def apply_links(projection: config.MachineProjection, dry_run: bool = False) -> list[core.StatusCheck]:
    return _project_checks(projection, apply=True, dry_run=dry_run)


def check_links(projection: config.MachineProjection) -> list[core.StatusCheck]:
    return _project_checks(projection, apply=False)


def link_project(repo: Path, path: Path) -> ProjectLinkReport:
    """Register a checkout locally and link its private Store Skills."""
    machine, hostname = config.resolve_machine()
    checks = []
    try:
        checkout = project_root(path)
        slug = project_slug(checkout)
        changed = _save_project(slug, checkout)
        checks.append(core.StatusCheck(kind="project", level="ok", text=f"{'registered' if changed else 'registered Project unchanged:'} {slug}: {checkout}", project=slug, target=str(checkout)))
        projection = config.load_machine_projection(repo)
        projection = dataclasses.replace(projection, projects=tuple(project for project in projection.projects if project.name == slug))
        checks.extend(apply_links(projection))
    except (ValueError, OSError, RuntimeError) as exc:
        checks.append(core.StatusCheck(kind="project", level="ERROR", text=core.one_line(str(exc)), target=str(path)))
    return ProjectLinkReport(machine_id=machine, hostname=hostname, repo=str(repo), checks=tuple(checks), exit_code=int(any(check.level in core.PROBLEM_LEVELS for check in checks)))
