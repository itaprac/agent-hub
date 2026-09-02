"""Content repository lookup, fleet configuration loading, and validation."""

from __future__ import annotations

import dataclasses
import os
import platform
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

REPO_ENV = "AGENT_HUB_REPO"
MACHINE_ENV = "AGENT_HUB_MACHINE"
POINTER_RELATIVE = Path(".config") / "agent-hub" / "root"
SAFE_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


class ConfigError(Exception):
    """A configuration error that is safe to show to the user."""


@dataclasses.dataclass(frozen=True)
class ProjectProjection:
    """One configured project as resolved for the current Machine."""

    name: str
    machines: tuple[tuple[str, str], ...]
    path: Path | None
    availability: Literal["available", "no_path", "missing", "not_directory"]
    reason: str

    @property
    def available(self) -> bool:
        return self.availability == "available"


@dataclasses.dataclass(frozen=True)
class AgentProjection:
    """One agent adapter with validated target templates."""

    name: str
    mode: Literal["symlink", "copy"]
    skills_global: str | None
    skills_project: str | None
    instructions_global: str | None
    instructions_project: str | None

    @property
    def target_templates(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (key, value)
            for key, value in (
                ("instructions_global", self.instructions_global),
                ("instructions_project", self.instructions_project),
                ("skills_global", self.skills_global),
                ("skills_project", self.skills_project),
            )
            if value is not None
        )


@dataclasses.dataclass(frozen=True)
class SkillTarget:
    """One selected Skill and its resolved target path."""

    agent: str
    project: str | None
    name: str
    source: Path
    target: Path
    mode: Literal["symlink", "copy"]


@dataclasses.dataclass(frozen=True)
class InstructionTarget:
    """Composed Instruction content and its resolved target path."""

    agent: str
    project: str | None
    sources: tuple[Path, ...]
    content: str
    target: Path


@dataclasses.dataclass(frozen=True)
class ManagedSkillDirectory:
    """A symlink-mode target directory and its expected entry names."""

    path: Path
    expected_entries: frozenset[str]


@dataclasses.dataclass(frozen=True)
class MachineProjection:
    """The immutable operational view of Fleet config for one Machine."""

    repo: Path
    machine_id: str
    hostname: str
    agents: tuple[AgentProjection, ...]
    projects: tuple[ProjectProjection, ...]
    skill_targets: tuple[SkillTarget, ...]
    instruction_targets: tuple[InstructionTarget, ...]
    managed_skill_directories: tuple[ManagedSkillDirectory, ...]
    projects_config_path: Path

    def has_project(self, name: str) -> bool:
        return any(project.name == name for project in self.projects)


def config_error(path: Path, key: str, message: str) -> ConfigError:
    return ConfigError(f"{path}: key '{key}': {message}")


# ------------------------------------------------------------------ repository

def app_root() -> Path:
    """The App repository root: the directory that holds the entry point scripts."""
    return Path(__file__).resolve().parents[1]


def repo_pointer_path() -> Path:
    """The machine-local file that records where the Content repository lives."""
    return Path(os.path.expanduser("~")) / POINTER_RELATIVE


def read_repo_pointer() -> str:
    path = repo_pointer_path()
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path}: cannot read the content repository pointer: {exc}") from exc
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    raise ConfigError(f"{path}: the content repository pointer is empty")


def repo_option_help() -> str:
    """The --repo help text, shared by the CLI and the web server."""
    return f"content repository root (default: {REPO_ENV}, then the path in {repo_pointer_path()})"


def require_directory(value: str | Path, source: str) -> Path:
    path = Path(os.path.expanduser(str(value))).resolve()
    if not path.is_dir():
        raise ConfigError(f"{source}: content repository directory not found: {path}")
    return path


def resolve_repo(explicit: str | Path | None = None) -> Path:
    """Find the Content repository: option, then environment, then pointer file."""
    if explicit is not None:
        return require_directory(explicit, "--repo")

    override = os.environ.get(REPO_ENV, "").strip()
    if override:
        return require_directory(override, REPO_ENV)

    pointer = read_repo_pointer()
    if pointer:
        return require_directory(pointer, str(repo_pointer_path()))

    # Compatibility default for the layout where the App and the Content still
    # share one repository. Remove this branch with the repository split, once
    # every machine resolves its Content through the pointer file.
    default = app_root()
    if (default / "config" / "hub.toml").is_file():
        return default.resolve()

    raise ConfigError(
        "no content repository configured; pass --repo PATH, set "
        f"{REPO_ENV}, or write the path into {repo_pointer_path()}"
    )


# --------------------------------------------------------------------- loading

def load_toml(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(f"{path}: missing configuration file")
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read TOML: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: top-level value must be a table")
    return value


def require_table(data: dict[str, Any], path: Path, key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise config_error(path, key, "must be a table")
    return value


def machine_name() -> str:
    return platform.node()


def short_hostname(hostname: str) -> str:
    lowered = hostname.lower()
    for suffix in (".local", ".lan"):
        if lowered.endswith(suffix):
            return hostname[: -len(suffix)]
    return hostname


def resolve_machine(hub_data: dict[str, Any], path: Path) -> tuple[str, str]:
    machines = require_table(hub_data, path, "machines")
    for key, value in machines.items():
        if not isinstance(value, str) or not value:
            raise config_error(path, f"machines.{key}", "must be a non-empty string")

    hostname = machine_name()
    override = os.environ.get(MACHINE_ENV, "").strip()
    if override:
        if override not in machines.values():
            raise config_error(
                path,
                "machines",
                f"{MACHINE_ENV} '{override}' is not a configured machine id",
            )
        return override, hostname
    shortened = short_hostname(hostname)
    candidates = [shortened]
    if shortened != hostname:
        candidates.append(hostname)
    for candidate in candidates:
        if candidate in machines:
            return machines[candidate], hostname

    suggested = shortened or hostname or "your-hostname"
    raise config_error(
        path,
        f"machines.{suggested}",
        f"unknown hostname '{hostname}'; add \"{suggested}\" = \"machine-id\" under [machines]",
    )


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value))


def validate_name(name: str, label: str) -> str:
    if SAFE_NAME.fullmatch(name) is None:
        raise ValueError(
            f"invalid {label} {name!r}; use lowercase ASCII letters and digits, "
            "with single '-' or '_' separators"
        )
    return name


def skill_directories(parent: Path) -> tuple[Path, ...]:
    """Return visible Skill directories in canonical order."""
    if not parent.is_dir():
        return ()
    result = []
    for child in sorted(parent.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        visible = (
            not any(part.startswith(".") for part in path.relative_to(child).parts)
            for path in child.rglob("*")
            if path.is_file()
        )
        if any(visible):
            result.append(child)
    return tuple(result)


def _format_target(
    template: str,
    agents_path: Path,
    agent: str,
    key: str,
    *,
    name: str = "",
    project: str = "",
    project_root: Path | None = None,
) -> Path:
    try:
        rendered = template.format(
            name=name, project=project, project_root=str(project_root or "")
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise config_error(agents_path, f"{agent}.{key}", f"invalid path template: {exc}") from exc
    return expand_path(rendered)


def _read_instruction_source(
    directory: Path, agent: str
) -> tuple[tuple[Path, ...], str] | None:
    sources = []
    parts = []
    for path in (directory / "base.md", directory / f"{agent}.md"):
        if path.is_file():
            sources.append(path)
            parts.append(path.read_text(encoding="utf-8").rstrip("\n"))
    if not parts:
        return None
    return tuple(sources), "\n\n".join(parts)


def _skill_is_selected(
    skill_assignments: dict[str, Any], machine_id: str, skill: str, agent: str
) -> bool:
    assignment = skill_assignments.get(skill)
    if assignment is None:
        return True
    return ("agents" not in assignment or agent in assignment["agents"]) and (
        "machines" not in assignment or machine_id in assignment["machines"]
    )


def _resolve_skill_targets(
    repo: Path,
    agents_path: Path,
    skill_assignments: dict[str, Any],
    machine_id: str,
    agent: AgentProjection,
    project: ProjectProjection | None,
) -> tuple[tuple[SkillTarget, ...], ManagedSkillDirectory | None]:
    if project is None:
        template = agent.skills_global
        key = "skills_global"
        source_parent = repo / "skills" / "global"
    else:
        template = agent.skills_project
        key = "skills_project"
        source_parent = repo / "skills" / "projects" / project.name
    if template is None:
        return (), None

    targets = tuple(
        SkillTarget(
            agent=agent.name,
            project=project.name if project is not None else None,
            name=source.name,
            source=source,
            target=_format_target(
                template,
                agents_path,
                agent.name,
                key,
                name=source.name,
                project=project.name if project is not None else "",
                project_root=project.path if project is not None else None,
            ),
            mode=agent.mode,
        )
        for source in skill_directories(source_parent)
        if _skill_is_selected(
            skill_assignments, machine_id, source.name, agent.name
        )
    )
    if agent.mode == "copy":
        return targets, None
    probe = _format_target(
        template,
        agents_path,
        agent.name,
        key,
        name="__agent_hub_skill__",
        project=project.name if project is not None else "",
        project_root=project.path if project is not None else None,
    )
    return targets, ManagedSkillDirectory(
        path=probe.parent,
        expected_entries=frozenset(
            target.target.name for target in targets if target.target.parent == probe.parent
        ),
    )


def _resolve_instruction_target(
    repo: Path,
    agents_path: Path,
    agent: AgentProjection,
    project: ProjectProjection | None,
) -> InstructionTarget | None:
    if project is None:
        template = agent.instructions_global
        key = "instructions_global"
        source_parent = repo / "instructions" / "global"
    else:
        template = agent.instructions_project
        key = "instructions_project"
        source_parent = repo / "instructions" / "projects" / project.name
    if template is None:
        return None

    instruction = _read_instruction_source(source_parent, agent.name)
    if instruction is None:
        return None
    sources, content = instruction
    return InstructionTarget(
        agent=agent.name,
        project=project.name if project is not None else None,
        sources=sources,
        content=content,
        target=_format_target(
            template,
            agents_path,
            agent.name,
            key,
            project=project.name if project is not None else "",
            project_root=project.path if project is not None else None,
        ),
    )


def _load_fleet_config(repo: Path) -> dict[str, Any]:
    repo = Path(repo).expanduser().resolve()
    hub_path = repo / "config" / "hub.toml"
    agents_path = repo / "config" / "agents.toml"
    projects_path = repo / "config" / "projects.toml"
    skills_path = repo / "config" / "skills.toml"

    hub_data = load_toml(hub_path)
    agents = load_toml(agents_path)
    projects = load_toml(projects_path)
    skills = load_toml(skills_path, required=False)
    machine_id, hostname = resolve_machine(hub_data, hub_path)

    known_agent_keys = {
        "skills_global",
        "skills_project",
        "instructions_global",
        "instructions_project",
        "mode",
    }
    for agent, agent_config in agents.items():
        if not isinstance(agent_config, dict):
            raise config_error(agents_path, agent, "must be a table")
        unknown = sorted(set(agent_config) - known_agent_keys)
        if unknown:
            raise config_error(agents_path, f"{agent}.{unknown[0]}", "unknown adapter key")
        mode = agent_config.get("mode", "symlink")
        if mode not in {"symlink", "copy"}:
            raise config_error(agents_path, f"{agent}.mode", "must be 'symlink' or 'copy'")
        for key in known_agent_keys - {"mode"}:
            if key in agent_config and not isinstance(agent_config[key], str):
                raise config_error(agents_path, f"{agent}.{key}", "must be a string path template")

    for project, paths in projects.items():
        if not isinstance(paths, dict):
            raise config_error(projects_path, project, "must be a table")
        for machine, value in paths.items():
            if not isinstance(value, str) or not value:
                raise config_error(
                    projects_path, f"{project}.{machine}", "must be a non-empty path string"
                )

    known_machine_ids = set(require_table(hub_data, hub_path, "machines").values())
    known_skill_keys = {"agents", "machines"}
    for skill, skill_assignment in skills.items():
        if not isinstance(skill_assignment, dict):
            raise config_error(skills_path, skill, "must be a table")
        unknown_keys = sorted(set(skill_assignment) - known_skill_keys)
        if unknown_keys:
            raise config_error(
                skills_path,
                f"{skill}.{unknown_keys[0]}",
                "unknown key; expected 'agents', 'machines', or both",
            )
        if not skill_assignment:
            raise config_error(
                skills_path,
                skill,
                "the section must contain 'agents', 'machines', or both",
            )
        if "agents" in skill_assignment:
            allowed_agents = skill_assignment["agents"]
            if not isinstance(allowed_agents, list) or not all(
                isinstance(item, str) for item in allowed_agents
            ):
                raise config_error(skills_path, f"{skill}.agents", "must be an array of agent names")
            unknown_agents = sorted(set(allowed_agents) - set(agents))
            if unknown_agents:
                raise config_error(
                    skills_path,
                    f"{skill}.agents",
                    f"unknown agent '{unknown_agents[0]}'; define it in {agents_path}",
                )
        if "machines" in skill_assignment:
            allowed_machines = skill_assignment["machines"]
            if not isinstance(allowed_machines, list) or not all(
                isinstance(item, str) for item in allowed_machines
            ):
                raise config_error(
                    skills_path, f"{skill}.machines", "must be an array of machine ids"
                )
            unknown_machines = sorted(set(allowed_machines) - known_machine_ids)
            if unknown_machines:
                raise config_error(
                    skills_path,
                    f"{skill}.machines",
                    f"unknown machine id '{unknown_machines[0]}'; define it in {hub_path}",
                )

    return {
        "repo": repo,
        "machine_id": machine_id,
        "hostname": hostname,
        "agents": agents,
        "projects": projects,
        "skills": skills,
        "paths": {
            "hub": hub_path,
            "agents": agents_path,
            "projects": projects_path,
            "skills": skills_path,
        },
    }


def load_machine_projection(repo: Path) -> MachineProjection:
    """Load and resolve Fleet config for the current Machine."""
    context = _load_fleet_config(repo)
    agents = tuple(
        AgentProjection(
            name=name,
            mode=agent_config.get("mode", "symlink"),
            skills_global=agent_config.get("skills_global"),
            skills_project=agent_config.get("skills_project"),
            instructions_global=agent_config.get("instructions_global"),
            instructions_project=agent_config.get("instructions_project"),
        )
        for name, agent_config in sorted(context["agents"].items())
    )
    agents_path = context["paths"]["agents"]
    for agent in agents:
        for key, template in agent.target_templates:
            _format_target(
                template,
                agents_path,
                agent.name,
                key,
                name="__agent_hub_skill__",
                project="__agent_hub_project__",
                project_root=Path("/__agent_hub_project__"),
            )
    projects: list[ProjectProjection] = []
    for name, machines in sorted(context["projects"].items()):
        raw_path = machines.get(context["machine_id"])
        if raw_path is None:
            path = None
            availability: Literal["available", "no_path", "missing", "not_directory"] = "no_path"
            reason = f"no path for machine '{context['machine_id']}'"
        else:
            path = expand_path(raw_path)
            if not path.exists():
                availability = "missing"
                reason = f"path does not exist: {path}"
            elif not path.is_dir():
                availability = "not_directory"
                reason = f"path is not a directory: {path}"
            else:
                availability = "available"
                reason = ""
        projects.append(
            ProjectProjection(
                name=name,
                machines=tuple(sorted(machines.items())),
                path=path,
                availability=availability,
                reason=reason,
            )
        )
    available_projects = tuple(project for project in projects if project.available)
    skill_targets: list[SkillTarget] = []
    managed_directories: dict[Path, set[str]] = {}
    for agent in agents:
        for project in (None, *available_projects):
            targets, managed = _resolve_skill_targets(
                context["repo"],
                agents_path,
                context["skills"],
                context["machine_id"],
                agent,
                project,
            )
            skill_targets.extend(targets)
            if managed is not None:
                managed_directories.setdefault(managed.path, set()).update(
                    managed.expected_entries
                )

    instruction_targets: list[InstructionTarget] = []
    for agent in agents:
        for project in (None, *available_projects):
            instruction = _resolve_instruction_target(
                context["repo"], agents_path, agent, project
            )
            if instruction is not None:
                instruction_targets.append(instruction)
    return MachineProjection(
        repo=context["repo"],
        machine_id=context["machine_id"],
        hostname=context["hostname"],
        agents=agents,
        projects=tuple(projects),
        skill_targets=tuple(skill_targets),
        instruction_targets=tuple(instruction_targets),
        managed_skill_directories=tuple(
            ManagedSkillDirectory(path=path, expected_entries=frozenset(expected))
            for path, expected in sorted(managed_directories.items(), key=lambda item: str(item[0]))
        ),
        projects_config_path=context["paths"]["projects"],
    )
