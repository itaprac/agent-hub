"""Project commands preserve checkout files and link private Skills from the Store."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import ROOT, git, write


def command(home: Path, content: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agenthub.cli", "--store", str(content), *args],
        cwd=cwd or ROOT,
        env=dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT)),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def succeeded(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def registry(home: Path) -> dict[str, str]:
    path = home / ".config" / "agent-hub" / "projects.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def registered_slug(home: Path, project: Path) -> str:
    records = registry(home)
    assert list(records.values()) == [str(project)]
    return next(iter(records))


def check_link(path: Path, destination: Path) -> None:
    assert path.is_symlink()
    assert path.resolve() == destination.resolve()
    assert not os.path.isabs(os.readlink(path))


@pytest.fixture
def checkout(project: Path) -> Path:
    write(project / "README.md", "Project documentation\n")
    write(project / "AGENTS.md", "Committed project instructions\n")
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.name", "Project tests")
    git(project, "config", "user.email", "project@example.invalid")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "project fixture")
    git(project, "remote", "add", "origin", "https://example.invalid/team/project.git")
    return project


def test_add_skill_project_path_registers_checkout_and_creates_private_template(content: Path, home: Path, checkout: Path) -> None:
    before = (checkout / "AGENTS.md").read_bytes()

    succeeded(command(home, content, "add-skill", "private-skill", "--project", str(checkout)))

    slug = registered_slug(home, checkout)
    skill = content / "projects" / slug / "skills" / "private-skill"
    assert "name: private-skill" in (skill / "SKILL.md").read_text()
    assert "description:" in (skill / "SKILL.md").read_text()
    assert not (content / "skills" / "private-skill").exists()
    succeeded(command(home, content, "apply"))
    check_link(checkout / ".agents" / "skills" / "private-skill", skill)
    check_link(checkout / ".claude" / "skills" / "private-skill", skill)
    assert (checkout / "AGENTS.md").read_bytes() == before
    assert git(checkout, "status", "--porcelain").stdout == ""


def test_project_link_defaults_to_current_checkout(content: Path, home: Path, checkout: Path) -> None:
    nested = checkout / "src"
    nested.mkdir()

    succeeded(command(home, content, "project", "link", cwd=nested))

    registered_slug(home, checkout)
    assert git(checkout, "status", "--porcelain").stdout == ""


def test_project_link_connects_existing_private_skills_and_is_idempotent(content: Path, home: Path, checkout: Path) -> None:
    succeeded(command(home, content, "project", "link", str(checkout)))
    slug = registered_slug(home, checkout)
    skill = content / "projects" / slug / "skills" / "private"
    write(skill / "SKILL.md", "Private Skill\n")
    before = (checkout / "AGENTS.md").read_bytes()

    succeeded(command(home, content, "project", "link", str(checkout)))
    exclude = (checkout / ".git" / "info" / "exclude").read_bytes()
    succeeded(command(home, content, "project", "link", str(checkout)))

    check_link(checkout / ".agents" / "skills" / "private", skill)
    check_link(checkout / ".claude" / "skills" / "private", skill)
    assert (checkout / ".git" / "info" / "exclude").read_bytes() == exclude
    assert (checkout / "AGENTS.md").read_bytes() == before
    assert git(checkout, "check-ignore", ".agents/skills/private", ".claude/skills/private").stdout.splitlines() == [
        ".agents/skills/private", ".claude/skills/private"
    ]
    assert git(checkout, "status", "--porcelain").stdout == ""


def test_adopt_project_derives_the_checkout_from_the_skill_source(content: Path, home: Path, checkout: Path) -> None:
    source = checkout / ".claude" / "skills" / "local-skill"
    write(source / "SKILL.md", "Local project Skill\n")
    write(source / "scripts" / "run.sh", "#!/bin/sh\nexit 0\n")
    (source / "scripts" / "run.sh").chmod(0o755)

    succeeded(command(home, content, "adopt", str(source), "--project", "--name", "private"))

    slug = registered_slug(home, checkout)
    skill = content / "projects" / slug / "skills" / "private"
    check_link(source, skill)
    assert (skill / "SKILL.md").read_text() == "Local project Skill\n"
    assert (skill / "scripts" / "run.sh").stat().st_mode & 0o777 == 0o755
    assert not (content / "skills" / "private").exists()
    succeeded(command(home, content, "apply"))
    check_link(checkout / ".agents" / "skills" / "private", skill)
    check_link(checkout / ".claude" / "skills" / "private", skill)
    assert git(checkout, "status", "--porcelain").stdout == ""


def test_adopt_project_collision_keeps_source_bytes(content: Path, home: Path, checkout: Path) -> None:
    succeeded(command(home, content, "add-skill", "private", "--project", str(checkout)))
    slug = registered_slug(home, checkout)
    existing = content / "projects" / slug / "skills" / "private" / "SKILL.md"
    before = existing.read_bytes()
    source = checkout / "local" / "private"
    write(source / "SKILL.md", "Do not lose this content\n")

    result = command(home, content, "adopt", str(source), "--project")

    assert result.returncode != 0
    assert source.is_dir() and not source.is_symlink()
    assert (source / "SKILL.md").read_text() == "Do not lose this content\n"
    assert existing.read_bytes() == before


def test_adopt_project_rejects_source_outside_a_git_checkout(content: Path, home: Path) -> None:
    source = home / "untracked-skill"
    write(source / "SKILL.md", "Keep outside checkout\n")

    result = command(home, content, "adopt", str(source), "--project")

    assert result.returncode != 0
    assert not source.is_symlink()
    assert (source / "SKILL.md").read_text() == "Keep outside checkout\n"
    assert registry(home) == {}


def test_project_without_origin_is_rejected_without_registering(content: Path, home: Path, checkout: Path) -> None:
    git(checkout, "remote", "remove", "origin")

    result = command(home, content, "add-skill", "private", "--project", str(checkout))

    assert result.returncode != 0
    assert "origin" in result.stdout + result.stderr
    assert registry(home) == {}
    assert not (checkout / ".agents").exists()


def test_apply_recreates_links_for_registered_projects(content: Path, home: Path, checkout: Path) -> None:
    succeeded(command(home, content, "add-skill", "private", "--project", str(checkout)))
    succeeded(command(home, content, "apply"))
    slug = registered_slug(home, checkout)
    skill = content / "projects" / slug / "skills" / "private"
    (checkout / ".agents" / "skills" / "private").unlink()
    (checkout / ".claude" / "skills" / "private").unlink()

    succeeded(command(home, content, "apply"))

    check_link(checkout / ".agents" / "skills" / "private", skill)
    check_link(checkout / ".claude" / "skills" / "private", skill)


def test_apply_skips_missing_registered_checkout(content: Path, home: Path, checkout: Path) -> None:
    succeeded(command(home, content, "project", "link", str(checkout)))
    saved = checkout.with_name("moved-checkout")
    checkout.rename(saved)

    result = command(home, content, "apply")

    succeeded(result)
    assert "[skip]" in result.stdout
    assert str(checkout) in result.stdout
    assert not checkout.exists()
    registered_slug(home, checkout)


def test_project_link_does_not_overwrite_a_real_target_directory(content: Path, home: Path, checkout: Path) -> None:
    succeeded(command(home, content, "project", "link", str(checkout)))
    slug = registered_slug(home, checkout)
    write(content / "projects" / slug / "skills" / "private" / "SKILL.md", "Store Skill\n")
    target = checkout / ".agents" / "skills" / "private"
    write(target / "SKILL.md", "Operator Skill\n")

    result = command(home, content, "project", "link", str(checkout))

    assert result.returncode != 0
    assert "[DRIFT]" in result.stdout
    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text() == "Operator Skill\n"


def test_adopt_project_rejects_a_skill_with_tracked_files(content: Path, home: Path, checkout: Path) -> None:
    source = checkout / ".claude" / "skills" / "committed"
    write(source / "SKILL.md", "Committed project Skill\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "commit project Skill")
    before = git(checkout, "rev-parse", "HEAD").stdout

    result = command(home, content, "adopt", str(source), "--project")

    assert result.returncode != 0
    assert "tracked" in result.stdout + result.stderr or "committed" in result.stdout + result.stderr
    assert source.is_dir() and not source.is_symlink()
    assert (source / "SKILL.md").read_text() == "Committed project Skill\n"
    assert git(checkout, "status", "--porcelain").stdout == ""
    assert git(checkout, "rev-parse", "HEAD").stdout == before


@pytest.mark.parametrize("operation", ["add-skill", "adopt"])
def test_project_skill_commands_reject_a_symlink_into_global_skills(content: Path, home: Path, checkout: Path, operation: str) -> None:
    succeeded(command(home, content, "project", "link", str(checkout)))
    slug = registered_slug(home, checkout)
    directory = content / "projects" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skills").symlink_to(content / "skills", target_is_directory=True)
    source = checkout / "private-source"
    if operation == "adopt":
        write(source / "SKILL.md", "Keep original private source\n")
        args = ("adopt", str(source), "--project", "--name", "private-added")
    else:
        args = ("add-skill", "private-added", "--project", str(checkout))

    result = command(home, content, *args)

    assert result.returncode != 0
    assert not (content / "skills" / "private-added").exists()
    assert (directory / "skills").is_symlink()
    if operation == "adopt":
        assert source.is_dir() and not source.is_symlink()
        assert (source / "SKILL.md").read_text() == "Keep original private source\n"
