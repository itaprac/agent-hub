"""Resolve the Agent table and Store filters for one Machine."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agenthub import config

from conftest import MACHINE_ID, write


def test_projection_does_not_parse_legacy_configuration(content: Path) -> None:
    for name in ("hub.toml", "agents.toml", "projects.toml", "skills.toml", "peers.toml"):
        write(content / "config" / name, "not = [valid TOML\n")
    for name in ("agents.toml", "projects.toml", "skills.toml", "peers.toml"):
        write(content / name, "not = [valid TOML\n")

    projection = config.load_machine_projection(content)

    assert dataclasses.is_dataclass(projection)
    assert projection.__dataclass_params__.frozen
    assert all(agent.__dataclass_params__.frozen for agent in projection.agents)
    assert projection.repo == content
    assert projection.projects == ()
    assert [target.name for target in projection.skill_targets] == ["alpha"]


def test_missing_hub_config_selects_detected_agents(content: Path, home: Path) -> None:
    (content / "hub.toml").unlink()
    (home / ".codex").mkdir()

    projection = config.load_machine_projection(content)

    selected = {agent.name for agent in projection.agents}
    assert "claude-code" in selected
    assert "codex" in selected
    assert "cursor" not in selected
    assert {target.agent for target in projection.skill_targets} == {"claude-code"}
    assert {target.agent for target in projection.instruction_targets} >= {"claude-code", "codex"}


def test_no_detected_agents_is_valid(content: Path, home: Path) -> None:
    (content / "hub.toml").unlink()
    (home / ".claude").rmdir()

    projection = config.load_machine_projection(content)

    assert projection.agents == ()
    assert projection.skill_targets == ()
    assert projection.instruction_targets == ()


def test_explicit_agents_replace_detection(content: Path, home: Path) -> None:
    write(content / "hub.toml", '[agents]\nenabled = ["codex"]\n')
    assert not (home / ".codex").exists()

    projection = config.load_machine_projection(content)

    assert [agent.name for agent in projection.agents] == ["codex"]
    assert projection.skill_targets == ()
    assert projection.managed_skill_directories == ()
    assert [target.target for target in projection.instruction_targets] == [
        home / ".codex" / "AGENTS.md"
    ]


def test_explicit_empty_agents_disable_all_targets(content: Path) -> None:
    write(content / "hub.toml", "[agents]\nenabled = []\n")
    projection = config.load_machine_projection(content)
    assert projection.agents == ()
    assert projection.skill_targets == ()
    assert projection.instruction_targets == ()


def test_projection_selects_and_orders_targets(content: Path, home: Path) -> None:
    write(
        content / "hub.toml",
        f'''[agents]
enabled = ["codex", "claude-code"]
mode = "copy"

[skills.alpha]
agents = ["claude-code"]
machines = ["{MACHINE_ID}"]

[skills.beta]
machines = ["another-machine"]
''',
    )
    write(content / "skills" / "global" / "beta" / "SKILL.md", "# beta\n")
    write(content / "instructions" / "global" / "claude-code.md", "Claude global\n")

    projection = config.load_machine_projection(content)

    assert [(agent.name, agent.mode) for agent in projection.agents] == [
        ("claude-code", "copy"),
        ("codex", "copy"),
    ]
    assert [
        (target.agent, target.project, target.name, target.mode, target.target)
        for target in projection.skill_targets
    ] == [("claude-code", None, "alpha", "copy", home / ".claude" / "skills" / "alpha")]
    assert [
        (target.agent, target.project, target.sources, target.content)
        for target in projection.instruction_targets
    ] == [
        (
            "claude-code",
            None,
            (
                content / "instructions" / "global" / "base.md",
                content / "instructions" / "global" / "claude-code.md",
            ),
            "Global base\n\nClaude global",
        ),
        (
            "codex",
            None,
            (content / "instructions" / "global" / "base.md",),
            "Global base",
        ),
    ]
    assert projection.managed_skill_directories == ()


def test_override_retains_unset_builtin_paths(content: Path, home: Path) -> None:
    write(
        content / "hub.toml",
        '''[agents]
enabled = ["claude-code"]

[agents.claude-code]
skills_global = "~/.custom-claude/skills"
''',
    )

    projection = config.load_machine_projection(content)

    assert [target.target for target in projection.skill_targets] == [
        home / ".custom-claude" / "skills" / "alpha"
    ]
    assert [target.target for target in projection.instruction_targets] == [
        home / ".claude" / "CLAUDE.md"
    ]
    assert [(item.path, item.expected_entries) for item in projection.managed_skill_directories] == [
        (home / ".custom-claude" / "skills", frozenset({"alpha"})),
    ]


def test_custom_agent_without_instructions_gets_only_skills(content: Path, home: Path) -> None:
    write(
        content / "hub.toml",
        '''[agents]
enabled = ["my-agent"]

[agents.my-agent]
name = "My Agent"
universal = false
skills_global = "~/.my-agent/skills"
''',
    )
    projection = config.load_machine_projection(content)
    assert [target.target for target in projection.skill_targets] == [
        home / ".my-agent" / "skills" / "alpha"
    ]
    assert projection.instruction_targets == ()


@pytest.mark.parametrize("key", ["agents", "machines"])
def test_empty_filter_list_excludes_skill(content: Path, key: str) -> None:
    with (content / "hub.toml").open("a", encoding="utf-8") as handle:
        handle.write(f"[skills.alpha]\n{key} = []\n")
    projection = config.load_machine_projection(content)
    assert projection.skill_targets == ()
    assert len(projection.instruction_targets) == 1
    assert projection.managed_skill_directories[0].expected_entries == frozenset()


def test_shared_skill_directory_keeps_skills_selected_by_either_agent(
    content: Path, home: Path
) -> None:
    from agenthub import core

    write(
        content / "hub.toml",
        '''[agents]
enabled = ["first", "second"]

[agents.first]
skills_global = "~/.shared/skills"

[agents.second]
skills_global = "~/.shared/skills"

[skills.alpha]
agents = ["first"]

[skills.beta]
agents = ["second"]
''',
    )
    write(content / "skills" / "global" / "beta" / "SKILL.md", "# beta\n")
    projection = config.load_machine_projection(content)
    directory = home / ".shared" / "skills"
    directory.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (directory / name).symlink_to(content / "skills" / "global" / name)

    assert list(core.iter_orphaned_skill_links(projection)) == []
    assert [(item.path, item.expected_entries) for item in projection.managed_skill_directories] == [
        (directory, frozenset({"alpha", "beta"})),
    ]
