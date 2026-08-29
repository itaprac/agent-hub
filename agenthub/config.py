"""Content repository lookup, fleet configuration loading, and validation."""

from __future__ import annotations

import os
import platform
import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ENV = "AGENT_HUB_REPO"
MACHINE_ENV = "AGENT_HUB_MACHINE"
POINTER_RELATIVE = Path(".config") / "agent-hub" / "root"
SAFE_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


class ConfigError(Exception):
    """A configuration error that is safe to show to the user."""


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


def load_context(repo: Path) -> dict[str, Any]:
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
    for agent, settings in agents.items():
        if not isinstance(settings, dict):
            raise config_error(agents_path, agent, "must be a table")
        unknown = sorted(set(settings) - known_agent_keys)
        if unknown:
            raise config_error(agents_path, f"{agent}.{unknown[0]}", "unknown adapter key")
        mode = settings.get("mode", "symlink")
        if mode not in {"symlink", "copy"}:
            raise config_error(agents_path, f"{agent}.mode", "must be 'symlink' or 'copy'")
        for key in known_agent_keys - {"mode"}:
            if key in settings and not isinstance(settings[key], str):
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
    for skill, settings in skills.items():
        if not isinstance(settings, dict):
            raise config_error(skills_path, skill, "must be a table")
        unknown_keys = sorted(set(settings) - known_skill_keys)
        if unknown_keys:
            raise config_error(
                skills_path,
                f"{skill}.{unknown_keys[0]}",
                "unknown key; expected 'agents', 'machines', or both",
            )
        if not settings:
            raise config_error(
                skills_path,
                skill,
                "the section must contain 'agents', 'machines', or both",
            )
        if "agents" in settings:
            allowed_agents = settings["agents"]
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
        if "machines" in settings:
            allowed_machines = settings["machines"]
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
