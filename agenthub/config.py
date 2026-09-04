"""Content repository lookup, fleet configuration loading, and validation."""

from __future__ import annotations

import dataclasses
import os
import platform
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

REPO_ENV = "AGENT_HUB_STORE"
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
    universal: bool = False
    detected: bool = False
    display_name: str = ""

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


def repo_option_help() -> str:
    """The Store lookup order, shared by the CLI and Console."""
    return f"Store directory (default: {REPO_ENV}, then ~/.agents)"


def resolve_repo(explicit: str | Path | None = None, *, create: bool = False) -> Path:
    """Resolve the Store without writing files; init may choose a missing path."""
    value = (
        explicit
        if explicit is not None
        else os.environ.get(REPO_ENV, "").strip() or "~/.agents"
    )
    try:
        path = expand_path(str(value)).resolve()
    except ValueError as exc:
        raise ConfigError(f"--store / {REPO_ENV}: {exc}") from exc
    if not create and not path.is_dir():
        raise ConfigError(
            f"--store / {REPO_ENV}: Store directory not found: {path}; run 'agent-hub init'"
        )
    return path


# --------------------------------------------------------------------- loading


def load_toml(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(f"{path}: missing configuration file")
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
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


def resolve_machine() -> tuple[str, str]:
    """Use the local pin or a sanitized short hostname for Machine identity."""
    hostname = machine_name()
    path = Path.home() / ".config/agent-hub/machine"
    if path.exists():
        try:
            machine = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ConfigError(f"{path}: cannot read Machine ID: {exc}") from exc
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", machine):
            raise ConfigError(
                f"{path}: invalid Machine ID; use lowercase letters, digits, and hyphens"
            )
    else:
        machine = re.sub(r"[^a-z0-9-]", "-", hostname.split(".", 1)[0].lower()).strip(
            "-"
        )
        if not machine:
            raise ConfigError(
                f"{path}: cannot derive Machine ID; write an ID to this file"
            )
    return machine, hostname


def expand_path(value: str) -> Path:
    from .agents import expand_path as expand

    return expand(value)


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
    return tuple(
        child
        for child in sorted(parent.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir()
        and not child.name.startswith(".")
        and any(
            path.is_file()
            and not any(part.startswith(".") for part in path.relative_to(child).parts)
            for path in child.rglob("*")
        )
    )


def load_settings(repo: Path) -> dict[str, Any]:
    """Read and validate the optional Store configuration."""
    from .agents import AgentError, load_agents

    path = repo / "hub.toml"
    data = load_toml(path, required=False)
    for key in data:
        if key not in {"agents", "skills"}:
            raise config_error(path, key, "unknown key; expected 'agents' or 'skills'")
    agent_data = data.get("agents", {})
    if not isinstance(agent_data, dict):
        raise config_error(path, "agents", "must be a table")
    mode = agent_data.get("mode", "symlink")
    if mode not in ("symlink", "copy"):
        raise config_error(path, "agents.mode", "must be 'symlink' or 'copy'")
    overrides = {
        key: value
        for key, value in agent_data.items()
        if key not in {"enabled", "mode"}
    }
    try:
        agents = load_agents(overrides, source=path)
    except AgentError as exc:
        raise ConfigError(str(exc)) from exc
    enabled = agent_data.get("enabled")
    if enabled is not None:
        if not isinstance(enabled, list) or not all(
            isinstance(item, str) for item in enabled
        ):
            raise config_error(path, "agents.enabled", "must be an array of Agent IDs")
        unknown = set(enabled) - agents.keys()
        if unknown:
            raise config_error(
                path, "agents.enabled", f"unknown Agent '{min(unknown)}'"
            )
    skills = data.get("skills", {})
    if not isinstance(skills, dict):
        raise config_error(path, "skills", "must be a table")
    for name, filters in skills.items():
        key = f"skills.{name}"
        if not isinstance(filters, dict):
            raise config_error(path, key, "must be a table")
        for field, values in filters.items():
            if field not in {"agents", "machines"}:
                raise config_error(path, f"{key}.{field}", "unknown filter")
            if not isinstance(values, list) or not all(
                isinstance(item, str) for item in values
            ):
                raise config_error(
                    path, f"{key}.{field}", "must be an array of strings"
                )
            if field == "agents" and (unknown := set(values) - agents.keys()):
                raise config_error(
                    path, f"{key}.{field}", f"unknown Agent '{min(unknown)}'"
                )
    return {"agents": agents, "enabled": enabled, "mode": mode, "skills": skills}


def _skill_is_selected(
    skill_assignments: dict[str, Any], machine_id: str, skill: str, agent: str
) -> bool:
    assignment = skill_assignments.get(skill, {})
    return ("agents" not in assignment or agent in assignment["agents"]) and (
        "machines" not in assignment or machine_id in assignment["machines"]
    )


def load_machine_projection(repo: Path, *, copy: bool = False) -> MachineProjection:
    """Resolve selected Agents and targets for the current Machine."""
    repo = Path(repo).expanduser().resolve()
    settings = load_settings(repo)
    machine_id, hostname = resolve_machine()
    enabled = settings["enabled"]
    agents = tuple(
        AgentProjection(
            name=agent.id,
            mode="copy" if copy else settings["mode"],
            skills_global=str(agent.skills_global) if agent.skills_global else None,
            skills_project=agent.skills_project,
            instructions_global=str(agent.instructions_global)
            if agent.instructions_global
            else None,
            instructions_project=None,
            universal=agent.universal,
            detected=agent.detected,
            display_name=agent.name,
        )
        for agent in sorted(settings["agents"].values(), key=lambda agent: agent.id)
        if (agent.id in enabled if enabled is not None else agent.detected)
    )
    skill_targets = []
    instruction_targets = []
    managed: dict[Path, set[str]] = {}
    skill_sources = skill_directories(repo / "skills")
    for agent in agents:
        if agent.skills_global and not agent.universal:
            directory = Path(agent.skills_global)
            targets = [
                SkillTarget(
                    agent.name,
                    None,
                    source.name,
                    source,
                    directory / source.name,
                    agent.mode,
                )
                for source in skill_sources
                if _skill_is_selected(
                    settings["skills"], machine_id, source.name, agent.name
                )
            ]
            skill_targets.extend(targets)
            if agent.mode == "symlink":
                managed.setdefault(directory, set()).update(
                    item.name for item in targets
                )
        if agent.instructions_global and (repo / "AGENTS.md").is_file():
            sources = tuple(
                path
                for path in (
                    repo / "AGENTS.md",
                    repo / f"agents/{agent.name}.md",
                )
                if path.is_file()
            )
            if sources:
                content = "\n\n".join(
                    path.read_text(encoding="utf-8").rstrip("\n") for path in sources
                )
                instruction_targets.append(
                    InstructionTarget(
                        agent.name,
                        None,
                        sources,
                        content,
                        Path(agent.instructions_global),
                    )
                )
    return MachineProjection(
        repo,
        machine_id,
        hostname,
        agents,
        (),
        tuple(skill_targets),
        tuple(instruction_targets),
        tuple(
            ManagedSkillDirectory(path, frozenset(names))
            for path, names in sorted(managed.items())
        ),
        Path.home() / ".config/agent-hub/projects.json",
    )
