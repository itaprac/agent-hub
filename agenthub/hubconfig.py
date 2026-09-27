"""Change `hub.toml` settings from the Console without losing the rest of the file.

Each change replaces only its own table or keys. The result must parse to the
old data plus the requested change; otherwise the file stays unchanged and the
operator edits it as text.
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import config, core, files, lifecycle

HUB = "hub.toml"
MODES = ("symlink", "copy")
MACHINE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class HubConfigError(ValueError):
    """A request that cannot change `hub.toml` safely."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ text edits


def _header_path(line: str) -> tuple[str, ...] | None:
    """The key path of a `[table]` header line, or None for any other line."""
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    try:
        data: Any = tomllib.loads(stripped)
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(data, dict) and len(data) == 1:
        key = next(iter(data))
        path.append(key)
        data = data[key]
    return tuple(path)


def _sections(lines: list[str]) -> list[tuple[tuple[str, ...], int, int]]:
    """(path, first line, end line) for the preamble and every table."""
    starts: list[tuple[tuple[str, ...], int]] = [((), 0)]
    for index, line in enumerate(lines):
        path = _header_path(line)
        if path is not None:
            starts.append((path, index))
    return [
        (path, start, starts[number + 1][1] if number + 1 < len(starts) else len(lines))
        for number, (path, start) in enumerate(starts)
    ]


def _key(value: str) -> str:
    return value if BARE_KEY.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value, ensure_ascii=False) for value in values) + "]"


def _trim_blank_end(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _set_skill_text(text: str, name: str, filters: dict[str, list[str]]) -> str:
    lines = text.splitlines()
    sections = _sections(lines)
    target = ("skills", name)
    kept: list[str] = []
    insert_at = None
    for path, start, end in sections:
        if path == target:
            insert_at = len(_trim_blank_end(kept))
            continue
        kept.extend(lines[start:end])
    if not filters:
        return "\n".join(_trim_blank_end(kept)) + "\n" if kept else ""
    table = [f"[skills.{_key(name)}]"] + [
        f"{field} = {_array(filters[field])}" for field in ("machines", "agents") if field in filters
    ]
    if insert_at is None:
        # After the last Skill table, or at the end of the file.
        last = [end for path, _, end in _sections(kept) if path[:1] == ("skills",) and path]
        insert_at = len(_trim_blank_end(kept[: last[-1]])) if last else len(_trim_blank_end(kept))
    before = _trim_blank_end(kept[:insert_at])
    after = kept[insert_at:]
    while after and not after[0].strip():
        after.pop(0)
    result = before + ([""] if before else []) + table + ([""] + after if after else [])
    return "\n".join(result) + "\n"


def _key_line(line: str, keys: set[str]) -> bool:
    match = re.match(r'^\s*"?([A-Za-z0-9_-]+)"?\s*=', line)
    return bool(match and match.group(1) in keys)


def _set_agents_text(text: str, enabled: list[str] | None, mode: str) -> str:
    lines = text.splitlines()
    sections = _sections(lines)
    found = next(((start, end) for path, start, end in sections if path == ("agents",)), None)
    new_keys = ([f"enabled = {_array(enabled)}"] if enabled is not None else []) + [f'mode = "{mode}"']
    if found is None:
        first_table = next((start for path, start, _ in sections if path), len(lines))
        before = _trim_blank_end(lines[:first_table])
        table = ["[agents]", *new_keys]
        rest = lines[first_table:]
        result = before + ([""] if before else []) + table + ([""] + rest if rest else [])
        return "\n".join(result) + "\n"
    start, end = found
    body: list[str] = []
    index = start + 1
    while index < end:
        line = lines[index]
        if _key_line(line, {"enabled", "mode"}):
            # A value may span lines, such as a long array; drop all of it.
            chunk = line
            while True:
                try:
                    tomllib.loads(chunk)
                    break
                except tomllib.TOMLDecodeError:
                    index += 1
                    if index >= end:
                        break
                    chunk += "\n" + lines[index]
            index += 1
            continue
        body.append(line)
        index += 1
    result = lines[: start + 1] + new_keys + body + lines[end:]
    return "\n".join(result) + "\n"


# ------------------------------------------------------------------ operations


def _names(values: Any, label: str, pattern: re.Pattern[str] | None = None) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise HubConfigError(400, f"{label} must be null or an array of strings")
    result = sorted({value.strip() for value in values if value.strip()})
    if not result:
        raise HubConfigError(400, f"choose at least one {label[:-1]}, or allow all")
    if pattern is not None and (bad := [value for value in result if not pattern.fullmatch(value)]):
        raise HubConfigError(400, f"invalid {label[:-1]}: {bad[0]}")
    return result


def _change(
    projection: config.MachineProjection,
    operation: str,
    edit_text: Any,
    edit_data: Any,
    summary: str,
) -> lifecycle.LifecycleReport:
    repo = projection.repo
    path = repo / HUB
    if path.is_symlink():
        raise HubConfigError(400, f"{path}: must not be a symlink")
    old_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    revision = files.read(repo, HUB)["revision"] if path.is_file() else None
    try:
        old_data = tomllib.loads(old_text)
    except tomllib.TOMLDecodeError as exc:
        raise HubConfigError(422, f"hub.toml is not valid TOML; fix it in Config first: {exc}") from exc
    expected = copy.deepcopy(old_data)
    edit_data(expected)
    new_text = edit_text(old_text)
    try:
        matches = tomllib.loads(new_text) == expected
    except tomllib.TOMLDecodeError:
        matches = False
    if not matches:
        raise HubConfigError(
            422, "hub.toml uses a layout that the Console cannot change safely; edit it in Config"
        )
    if new_text == old_text:
        checks = [core.StatusCheck(kind="config", level="ok", text=f"{summary}; hub.toml unchanged")]
        return lifecycle._report(projection, operation, checks, apply=False)
    files.write(repo, HUB, new_text, revision)
    try:
        config.load_settings(repo)
    except config.ConfigError as exc:
        files.write(repo, HUB, old_text, files.read(repo, HUB)["revision"])
        raise HubConfigError(422, str(exc)) from exc
    checks = [core.StatusCheck(kind="config", level="ok", text=f"{summary}; updated hub.toml")]
    return lifecycle._report(projection, operation, checks)


def set_skill_targets(
    projection: config.MachineProjection, name: Any, machines: Any, agents: Any
) -> lifecycle.LifecycleReport:
    """Set or clear the Machines and Agents filter of one Skill name."""
    name = lifecycle._plain_name(name, "skill name")
    filters: dict[str, list[str]] = {}
    if (chosen := _names(machines, "machines", MACHINE_ID)) is not None:
        filters["machines"] = chosen
    if (chosen := _names(agents, "agents")) is not None:
        filters["agents"] = chosen

    def edit_data(data: dict[str, Any]) -> None:
        skills = data.setdefault("skills", {})
        if filters:
            skills[name] = filters
        else:
            skills.pop(name, None)
            if not skills:
                data.pop("skills")

    parts = [
        "machines " + ", ".join(filters["machines"]) if "machines" in filters else "all Machines",
        "agents " + ", ".join(filters["agents"]) if "agents" in filters else "all Agents",
    ]
    return _change(
        projection,
        "skill targets",
        lambda text: _set_skill_text(text, name, filters),
        edit_data,
        f"{name}: {'; '.join(parts)}",
    )


def set_agents(
    projection: config.MachineProjection, enabled: Any, mode: Any
) -> lifecycle.LifecycleReport:
    """Set the Agents that Apply targets and the deploy mode."""
    chosen = _names(enabled, "agents")
    if mode not in MODES:
        raise HubConfigError(400, "mode must be symlink or copy")

    def edit_data(data: dict[str, Any]) -> None:
        agents = data.setdefault("agents", {})
        agents.pop("enabled", None)
        if chosen is not None:
            agents["enabled"] = chosen
        agents["mode"] = mode

    summary = ("Agents " + ", ".join(chosen) if chosen is not None else "detected Agents") + f"; mode {mode}"
    return _change(
        projection,
        "agents",
        lambda text: _set_agents_text(text, chosen, mode),
        edit_data,
        summary,
    )


def machines(repo: Path, machine_id: str) -> list[dict[str, Any]]:
    """Machine IDs known from records, remote control, and this Machine."""
    from . import remote

    known: dict[str, list[str]] = {machine_id: []}
    for path in sorted((repo / "machines").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        name = record.get("machine") if isinstance(record, dict) else None
        if isinstance(name, str) and MACHINE_ID.fullmatch(name):
            agents = record.get("agents")
            known[name] = [agent for agent in agents if isinstance(agent, str)] if isinstance(agents, list) else []
    try:
        for name in remote.configured_machines():
            known.setdefault(name, [])
    except Exception:  # remote configuration is optional here
        pass
    return [{"machine": name, "agents": agents, "local": name == machine_id} for name, agents in sorted(known.items())]
