#!/usr/bin/env python3
"""Refresh skill paths from the skills.sh README and keep local Agent metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REF = "435076e78988e1e6ec40d00b0b1d76bdbbc5419a"


def parse_agents(source: str, previous: dict) -> dict:
    """Read the supported-agents table without executing upstream code."""
    begin, end = "<!-- supported-agents:start -->", "<!-- supported-agents:end -->"
    if begin not in source or end not in source:
        raise ValueError("upstream supported-agents table was not found")
    section = source.split(begin, 1)[1].split(end, 1)[0]
    agents = {}
    for line in section.splitlines():
        if not line.startswith("| ") or "`--agent`" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            raise ValueError(f"unsupported upstream Agent row: {line}")
        names, identifiers, project_cell, global_cell = cells
        ids = re.findall(r"`([a-z0-9-]+)`", identifiers)
        display_names = names.split(", ")
        project = re.fullmatch(r"`([^`]+)`", project_cell)
        global_match = re.fullmatch(r"`([^`]+)`", global_cell)
        if not ids or len(ids) != len(display_names) or not project:
            raise ValueError(f"unsupported upstream Agent row: {line}")
        if global_match is None and global_cell != "N/A (project-only)":
            raise ValueError(f"unsupported upstream global path: {global_cell}")
        for agent_id, name in zip(ids, display_names):
            if agent_id in agents:
                raise ValueError(f"duplicate upstream Agent: {agent_id}")
            skills_global = global_match[1].rstrip("/") if global_match else None
            old = previous.get(agent_id, {})
            old_path = old.get("skills_global")
            # Keep environment variables when the README still lists their default.
            if isinstance(old_path, str):
                default = re.sub(r"\$\{\w+:-([^{}]+)\}", r"\1", old_path)
                if default == skills_global:
                    skills_global = old_path
            # Codex reads the canonical global Store natively, per SPEC.md.
            if agent_id == "codex":
                skills_global = "~/.agents/skills"
            detect = old.get("detect")
            if detect is None:
                detect = [skills_global.rsplit("/", 1)[0]] if skills_global else []
            agents[agent_id] = {
                "name": name,
                "universal": skills_global == "~/.agents/skills",
                "skills_global": skills_global,
                "skills_project": project[1].rstrip("/"),
                "instructions_global": old.get("instructions_global"),
                "detect": detect,
            }
    if not agents:
        raise ValueError("upstream Agent table is empty")
    return dict(sorted(agents.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=UPSTREAM_REF, help="upstream commit, tag, or branch")
    parser.add_argument("--source", type=Path, help="read a local upstream README.md")
    parser.add_argument("--output", type=Path, default=ROOT / "agenthub/agents.json")
    args = parser.parse_args()
    if args.source:
        source = args.source.read_text(encoding="utf-8")
    else:
        url = f"https://raw.githubusercontent.com/vercel-labs/skills/{args.ref}/README.md"
        with urlopen(url, timeout=30) as response:
            source = response.read().decode("utf-8")
    metadata_path = args.output if args.output.exists() else ROOT / "agenthub/agents.json"
    previous = json.loads(metadata_path.read_text(encoding="utf-8"))
    table = parse_agents(source, previous)
    args.output.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(table)} Agents to {args.output}")


if __name__ == "__main__":
    main()
