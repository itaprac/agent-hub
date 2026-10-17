"""Local Project registration and private Skill links in Git checkouts."""

from __future__ import annotations

from collections.abc import Sequence
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


@dataclasses.dataclass(frozen=True)
class ProjectUnlinkReport(core.Report):
    @property
    def command(self) -> str:
        return "project unlink"


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


def _save_project(slug: str, checkout: Path | None) -> bool:
    """Record or, with no checkout, forget one Project in the local registry."""
    registry = load_projects()
    if registry.get(slug) == checkout:
        return False
    if checkout is None:
        del registry[slug]
    else:
        registry[slug] = checkout
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({name: str(value) for name, value in sorted(registry.items())}, indent=2)
    fileio.atomic_write(path, (data + "\n").encode(), 0o600)
    return True


def _common_dir(checkout: Path) -> Path:
    common = Path(_git(checkout, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = checkout / common
    # Git can place linked-worktree metadata outside the checkout. Trust only
    # its reported common directory, and reject symlinks within that directory.
    if common.is_symlink():
        raise ValueError(f"{common}: Git directory must not be a symlink")
    return common.resolve()


def _exclude_file(checkout: Path) -> Path:
    common = _common_dir(checkout)
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


def _directories(projection: config.MachineProjection, checkout: Path, *, every_agent: bool = False) -> dict[Path, set[str]]:
    result: dict[Path, set[str]] = {checkout / ".agents" / "skills": set()}
    # Unlinking also cleans directories of Agents that are no longer selected.
    agents = (
        [(agent.id, agent.universal, agent.skills_project) for agent in projection.settings["agents"].values()]
        if every_agent
        else [(agent.name, agent.universal, agent.skills_project) for agent in projection.agents]
    )
    for name, universal, skills_project in agents:
        if not universal and skills_project:
            result.setdefault(checkout / skills_project, set()).add(name)
    return result


def worktrees(checkout: Path) -> list[Path]:
    """List the recorded checkout first, then every other worktree Git has checked out."""
    output = _git(checkout, "worktree", "list", "--porcelain", "-z")
    result = [checkout]
    for record in output.split("\0\0"):
        fields = [field for field in record.split("\0") if field]
        if not fields or not fields[0].startswith("worktree "):
            continue
        # Bare entries have no files, and prunable ones lost their directory.
        if any(field == "bare" or field.split(" ", 1)[0] == "prunable" for field in fields[1:]):
            continue
        path = Path(fields[0].removeprefix("worktree "))
        if not path.is_absolute() or not path.is_dir():
            continue
        # Resolve only the parents: a symlinked worktree root stays visible so
        # validation can reject it instead of following it elsewhere.
        path = path.parent.resolve() / path.name
        if path not in result:
            result.append(path)
    return result


def _main_worktree(checkout: Path) -> Path:
    """Record the main checkout, which outlives the linked worktrees made from it."""
    output = _git(checkout, "worktree", "list", "--porcelain", "-z")
    fields = output.split("\0\0", 1)[0].split("\0")
    if not fields[0].startswith("worktree ") or "bare" in fields[1:]:
        return checkout
    main = Path(fields[0].removeprefix("worktree "))
    if not main.is_absolute() or not main.is_dir() or main.is_symlink():
        return checkout
    main = main.resolve()
    try:
        return main if project_root(main) == main and project_slug(main) == project_slug(checkout) else checkout
    except (ValueError, gitio.GitError):
        return checkout


def _checkout_checks(
    projection: config.MachineProjection, project: config.ProjectProjection, checkout: Path,
    sources: Sequence[Path], *, apply: bool, dry_run: bool, every_agent: bool = False,
) -> list[core.StatusCheck]:
    """Link one worktree's Project skills and prune links to Skills no longer selected."""
    settings = projection.settings
    selected_agents = {agent.name for agent in projection.agents}
    source_root = projection.repo / "projects" / project.name / "skills"
    checks = []
    deployed = []
    for directory, agent_ids in _directories(projection, checkout, every_agent=every_agent).items():
        try:
            _safe_descendant(checkout, directory / "placeholder")
        except ValueError as exc:
            checks.append(core.StatusCheck(kind="project", level="DRIFT", text=str(exc), project=project.name, target=str(directory)))
            continue
        expected = set()
        expected_sources = set()
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
    return checks


def _project_checks(
    projection: config.MachineProjection, *, apply: bool, dry_run: bool = False, unlink: bool = False,
) -> list[core.StatusCheck]:
    checks = []
    for project in projection.projects:
        if not project.available:
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
            # Unlinking keeps no Skill selected, so every owned link is pruned.
            sources: Sequence[Path] = () if unlink else config.skill_directories(source_root)
            if not sources and not apply:
                checks.append(core.StatusCheck(
                    kind="project", level="skip",
                    text=f"project {project.name}: no Project skills in the Store; agent-hub project unlink removes the record",
                    project=project.name, target=str(checkout),
                ))
            common = _common_dir(checkout)
            for worktree in worktrees(checkout):
                try:
                    # Linked worktrees share the origin and the Git exclude with
                    # the recorded checkout, but must be real roots of that repository.
                    if worktree != checkout and (
                        worktree.is_symlink() or project_root(worktree) != worktree
                        or _common_dir(worktree) != common
                    ):
                        raise ValueError(f"{worktree}: Project worktree must be a checkout root of {checkout}")
                    checks.extend(_checkout_checks(
                        projection, project, worktree, sources,
                        apply=apply, dry_run=dry_run, every_agent=unlink,
                    ))
                except (ValueError, OSError, RuntimeError) as exc:
                    checks.append(core.StatusCheck(kind="project", level="DRIFT", text=core.one_line(str(exc)), project=project.name, target=str(worktree)))
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
        checkout = _main_worktree(checkout)
        changed = _save_project(slug, checkout)
        checks.append(core.StatusCheck(kind="project", level="ok", text=f"{'registered' if changed else 'registered Project unchanged:'} {slug}: {checkout}", project=slug, target=str(checkout)))
        projection = config.load_machine_projection(repo)
        projection = dataclasses.replace(projection, projects=tuple(project for project in projection.projects if project.name == slug))
        checks.extend(apply_links(projection))
    except (ValueError, OSError, RuntimeError) as exc:
        checks.append(core.StatusCheck(kind="project", level="ERROR", text=core.one_line(str(exc)), target=str(path)))
    return ProjectLinkReport(machine_id=machine, hostname=hostname, repo=str(repo), checks=tuple(checks), exit_code=int(any(check.level in core.PROBLEM_LEVELS for check in checks)))


def unlink_project(repo: Path, target: str) -> ProjectUnlinkReport:
    """Remove a Project's links from its worktrees and forget its local record.

    TARGET is a recorded slug or a path inside the checkout. The Store's Skills
    and the Git exclude lines stay; a later ``project link`` restores the links.
    """
    machine, hostname = config.resolve_machine()
    checks = []
    try:
        registry = load_projects()
        slug = target if target in registry else project_slug(Path(target))
        if slug not in registry:
            raise ValueError(f"project {slug} is not recorded on this Machine")
        projection = config.load_machine_projection(repo)
        projection = dataclasses.replace(projection, projects=tuple(project for project in projection.projects if project.name == slug))
        checks.extend(_project_checks(projection, apply=True, unlink=True))
        if any(check.level in core.PROBLEM_LEVELS for check in checks):
            # Keep the record so the links can be removed once the problem is fixed.
            checks.append(core.StatusCheck(kind="project", level="ERROR", text=f"kept the record for {slug}; fix the problems above and run project unlink again", project=slug, target=str(registry[slug])))
        else:
            _save_project(slug, None)
            checks.append(core.StatusCheck(kind="project", level="ok", text=f"forgot {slug}: {registry[slug]}", project=slug, target=str(registry[slug])))
    except (ValueError, OSError, RuntimeError) as exc:
        checks.append(core.StatusCheck(kind="project", level="ERROR", text=core.one_line(str(exc)), target=target))
    return ProjectUnlinkReport(machine_id=machine, hostname=hostname, repo=str(repo), checks=tuple(checks), exit_code=int(any(check.level in core.PROBLEM_LEVELS for check in checks)))
