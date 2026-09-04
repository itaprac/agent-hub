"""Agent paths from the packaged table, with optional Store overrides."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
import json
import os
from pathlib import Path
import re
from typing import Any


class AgentError(ValueError):
    """An invalid Agent definition or path."""


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^{}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_AGENT_ID = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_FIELDS = {"name", "universal", "skills_global", "skills_project", "instructions_global", "detect"}


def expand_path(value: str) -> Path:
    """Expand ~, $VAR, ${VAR}, and ${VAR:-default} without executing a shell."""
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(3)
        environment = os.environ.get(name, "").strip()
        if environment:
            return environment
        if match.group(2) is not None:
            return match.group(2)
        raise AgentError(f"environment variable '{name}' is not set")

    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AgentError("must be a non-empty path string without NUL characters")
    expanded = _ENV.sub(substitute, value)
    if "$" in expanded:
        raise AgentError("unsupported or unresolved environment variable in path")
    expanded = os.path.expanduser(expanded)
    if expanded.startswith("~"):
        raise AgentError("cannot expand home directory")
    return Path(expanded).absolute()


@dataclass(frozen=True)
class Agent:
    """One Agent with resolved global paths and a relative project skill path."""

    id: str
    name: str
    universal: bool
    skills_global: Path | None
    skills_project: str | None
    instructions_global: Path | None
    detect: tuple[Path, ...]

    @property
    def detected(self) -> bool:
        return any(path.exists() for path in self.detect)


def load_agents(
    overrides: Mapping[str, Any] | None = None,
    *,
    source: str | Path = "hub.toml",
) -> dict[str, Agent]:
    """Load all Agents; override keys must be Agent IDs, without enabled or mode."""
    table = json.loads(files("agenthub").joinpath("agents.json").read_text(encoding="utf-8"))
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, Mapping):
        raise AgentError(f"{source}: key 'agents': must be a table")
    for agent_id, override in overrides.items():
        key = f"agents.{agent_id}"
        if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
            raise AgentError(f"{source}: key '{key}': invalid Agent ID")
        if not isinstance(override, Mapping):
            raise AgentError(f"{source}: key '{key}': must be a table")
        unknown = set(override) - _FIELDS
        if unknown:
            raise AgentError(f"{source}: key '{key}.{sorted(unknown)[0]}': unknown key")
        is_custom = agent_id not in table
        row = dict(table.get(agent_id, {"name": agent_id, "universal": False,
                    "skills_global": None, "skills_project": None,
                    "instructions_global": None, "detect": []}))
        row.update(override)
        if is_custom and "detect" not in override:
            path = row.get("skills_global") or row.get("instructions_global")
            if isinstance(path, str) and "/" in path:
                row["detect"] = [path.rsplit("/", 1)[0]]
        table[agent_id] = row

    result = {}
    for agent_id, row in sorted(table.items()):
        origin = source if agent_id in overrides else "agenthub/agents.json"

        def fail(field: str, message: str) -> None:
            raise AgentError(f"{origin}: key 'agents.{agent_id}.{field}': {message}")

        if not isinstance(row.get("name"), str) or not row["name"].strip():
            fail("name", "must be a non-empty string")
        if not isinstance(row.get("universal"), bool):
            fail("universal", "must be a boolean")

        def global_path(field: str) -> Path | None:
            value = row.get(field)
            if value is None:
                return None
            try:
                return expand_path(value)
            except AgentError as exc:
                fail(field, str(exc))
            return None

        project = row.get("skills_project")
        if project is not None:
            if (not isinstance(project, str) or not project.strip() or "\x00" in project
                    or Path(project).is_absolute() or ".." in Path(project).parts
                    or project.startswith("~") or "$" in project or "\\" in project
                    or ":" in project or Path(project) == Path(".")):
                fail("skills_project", "must be a relative path inside the project")
        skills_global = global_path("skills_global")
        instructions_global = global_path("instructions_global")
        detect = row.get("detect", [])
        if not isinstance(detect, list):
            fail("detect", "must be an array of path strings")
        resolved_detect = []
        for index, path in enumerate(detect):
            try:
                resolved_detect.append(expand_path(path))
            except AgentError as exc:
                fail(f"detect.{index}", str(exc))
        result[agent_id] = Agent(
            id=agent_id, name=row["name"], universal=row["universal"],
            skills_global=skills_global, skills_project=project,
            instructions_global=instructions_global,
            detect=tuple(resolved_detect),
        )
    return result
