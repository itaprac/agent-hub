"""Machine projection: immutable fleet config resolved for one Machine."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agenthub import config

from conftest import MACHINE_ID, write


def test_projection_contains_every_project_with_an_explicit_availability(
    content: Path, project: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("x\n", encoding="utf-8")
    write(
        content / "config" / "projects.toml",
        (
            f'[available]\n{MACHINE_ID} = "{project}"\n\n'
            '[no-path]\nother-machine = "~/elsewhere"\n\n'
            f'[missing]\n{MACHINE_ID} = "{missing}"\n\n'
            f'[not-directory]\n{MACHINE_ID} = "{regular_file}"\n'
        ),
    )

    projection = config.load_machine_projection(content)

    assert dataclasses.is_dataclass(projection)
    assert projection.repo == content
    assert [item.name for item in projection.projects] == [
        "available",
        "missing",
        "no-path",
        "not-directory",
    ]
    assert [(item.name, item.availability, item.reason) for item in projection.projects] == [
        ("available", "available", ""),
        ("missing", "missing", f"path does not exist: {missing}"),
        ("no-path", "no_path", f"no path for machine '{MACHINE_ID}'"),
        ("not-directory", "not_directory", f"path is not a directory: {regular_file}"),
    ]
    assert all(item.__dataclass_params__.frozen for item in projection.projects)


def test_projection_selects_and_orders_targets(content: Path, home: Path, project: Path) -> None:
    write(
        content / "config" / "agents.toml",
        """[claude]
skills_global = "~/.claude/skills/{name}"
skills_project = "{project_root}/.claude/skills/{name}"
instructions_global = "~/.claude/CLAUDE.md"
instructions_project = "{project_root}/CLAUDE.md"
mode = "copy"

[codex]
skills_global = "~/.codex/skills/{name}"
skills_project = "{project_root}/.codex/skills/{name}"
instructions_global = "~/.codex/AGENTS.md"
instructions_project = "{project_root}/AGENTS.md"
""",
    )
    write(
        content / "config" / "skills.toml",
        f'alpha = {{ agents = ["claude"] }}\nbeta = {{ agents = ["codex"], machines = ["{MACHINE_ID}"] }}\n',
    )
    write(content / "instructions" / "global" / "claude.md", "Claude global\n")
    write(content / "instructions" / "projects" / "demo" / "codex.md", "Codex project\n")

    projection = config.load_machine_projection(content)

    assert [(agent.name, agent.mode) for agent in projection.agents] == [
        ("claude", "copy"),
        ("codex", "symlink"),
    ]
    assert [
        (target.agent, target.project, target.name, target.mode)
        for target in projection.skill_targets
    ] == [
        ("claude", None, "alpha", "copy"),
        ("codex", "demo", "beta", "symlink"),
    ]
    assert [
        (target.agent, target.project, target.sources, target.content)
        for target in projection.instruction_targets
    ] == [
        (
            "claude",
            None,
            (
                content / "instructions" / "global" / "base.md",
                content / "instructions" / "global" / "claude.md",
            ),
            "Global base\n\nClaude global",
        ),
        (
            "claude",
            "demo",
            (content / "instructions" / "projects" / "demo" / "base.md",),
            "Project base",
        ),
        (
            "codex",
            None,
            (content / "instructions" / "global" / "base.md",),
            "Global base",
        ),
        (
            "codex",
            "demo",
            (
                content / "instructions" / "projects" / "demo" / "base.md",
                content / "instructions" / "projects" / "demo" / "codex.md",
            ),
            "Project base\n\nCodex project",
        ),
    ]
    assert [(item.path, item.expected_entries) for item in projection.managed_skill_directories] == [
        (home / ".codex" / "skills", frozenset()),
        (project / ".codex" / "skills", frozenset({"beta"})),
    ]


@pytest.mark.parametrize(
    "template", ["{unknown}", "{}", "{name.foo}", "{project_root[bad]}"]
)
def test_projection_rejects_an_invalid_dormant_path_template(
    content: Path, template: str
) -> None:
    write(
        content / "config" / "agents.toml",
        f'[claude]\ninstructions_project = "{template}"\n',
    )
    write(
        content / "config" / "projects.toml",
        '[absent]\nother-machine = "~/elsewhere"\n',
    )

    with pytest.raises(config.ConfigError, match="invalid path template"):
        config.load_machine_projection(content)
