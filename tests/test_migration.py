"""Migrate a real v1 layout without losing files, history, or private project scope."""

import json
from pathlib import Path
import tomllib

import pytest

from agenthub import config, gitio
from agenthub.migration import migrate
from conftest import git, write


@pytest.fixture
def legacy(tmp_path, home, monkeypatch):
    monkeypatch.setattr(config, "machine_name", lambda: "Mini.local")
    checkout = tmp_path / "project"
    checkout.mkdir()
    git(checkout, "init", "-b", "main")
    git(checkout, "remote", "add", "origin", "git@example.test:Owner/Project.git")
    repo = tmp_path / "v1-content"
    write(repo / "skills/global/alpha/SKILL.md", "# Original global alpha\n")
    write(repo / "skills/projects/demo/beta/SKILL.md", "# Private beta\n")
    write(repo / "instructions/global/base.md", "Original global base\n")
    write(repo / "instructions/global/claude.md", "Original Claude overlay\n")
    write(repo / "instructions/projects/demo/base.md", "Private project instructions\n")
    write(repo / "config/hub.toml", '[machines]\n"Mini" = "macmini"\n')
    write(
        repo / "config/projects.toml",
        f"[demo]\nmacmini = {json.dumps(str(checkout))}\n",
    )
    write(
        repo / "config/agents.toml",
        '[claude]\nskills_global = "~/.claude/skills/{name}"\n',
    )
    write(
        repo / "config/peers.toml", '[peers]\nmacmini = "http://example.invalid:7337"\n'
    )
    write(
        repo / "config/skills.toml",
        '[alpha]\nagents = ["claude"]\nmachines = ["macmini"]\n',
    )
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Migration tests")
    git(repo, "config", "user.email", "migration-tests@example.invalid")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "original v1 content")
    return repo


def assert_ok(report):
    assert report.exit_code == 0, report.lines()
    assert report.command == "migrate"


def snapshot(repo):
    return {
        str(path.relative_to(repo)): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def test_migration_moves_global_project_and_instruction_files_with_history(legacy):
    old_head = git(legacy, "rev-parse", "HEAD").stdout.strip()
    report = migrate(legacy)
    assert_ok(report)
    paths = {
        "skills/alpha/SKILL.md": "# Original global alpha\n",
        "projects/example.test--owner--project/skills/beta/SKILL.md": "# Private beta\n",
        "AGENTS.md": "Original global base\n",
        "agents/claude-code.md": "Original Claude overlay\n",
        "instructions/projects/demo/base.md": "Private project instructions\n",
    }
    for relative, expected in paths.items():
        assert (legacy / relative).read_text() == expected
        history = git(legacy, "log", "--follow", "--format=%H", "--", relative).stdout
        assert old_head in history, relative
    assert (
        git(legacy, "log", "-1", "--format=%s").stdout.strip()
        == "migrate: v1 to v2 layout"
    )
    assert git(legacy, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    assert git(legacy, "status", "--porcelain").stdout == ""
    for name in ("agents", "projects", "skills", "peers", "hub"):
        assert not (legacy / "config" / f"{name}.toml").exists()
    settings = tomllib.loads((legacy / "hub.toml").read_text())
    assert settings["skills"]["alpha"] == {
        "agents": ["claude-code"],
        "machines": ["macmini"],
    }
    assert any(
        "instructions/projects" in check.text and "manual" in check.text
        for check in report.checks
    )
    assert any("legacy Machine ID is macmini" in check.text for check in report.checks)


def test_migration_rerun_is_a_noop(legacy):
    assert_ok(migrate(legacy))
    before = git(legacy, "rev-parse", "HEAD").stdout
    assert_ok(migrate(legacy))
    assert git(legacy, "rev-parse", "HEAD").stdout == before
    assert git(legacy, "status", "--porcelain").stdout == ""


def test_unmapped_project_is_kept_outside_global_skill_detection(legacy):
    write(
        legacy / "skills/projects/unmapped/secret/SKILL.md",
        "Private unmapped content\n",
    )
    git(legacy, "add", "-A")
    git(legacy, "commit", "-m", "private unmapped Skill")
    old_head = git(legacy, "rev-parse", "HEAD").stdout.strip()
    assert_ok(migrate(legacy))
    retained = "migration-unmapped/projects/unmapped/skills/secret/SKILL.md"
    assert (legacy / retained).read_text() == "Private unmapped content\n"
    assert (
        old_head in git(legacy, "log", "--follow", "--format=%H", "--", retained).stdout
    )
    assert {path.name for path in config.skill_directories(legacy / "skills")} == {
        "alpha"
    }
    assert all(
        target.name != "projects"
        for target in config.load_machine_projection(legacy).skill_targets
    )


@pytest.mark.parametrize(
    "collision", ["AGENTS.md", "skills/alpha/SKILL.md", "agents/claude-code.md"]
)
def test_collisions_leave_every_file_and_index_unchanged(legacy, collision):
    write(legacy / collision, "Independent operator content\n")
    git(legacy, "add", "-A")
    git(legacy, "commit", "-m", "independent v2 file")
    before = snapshot(legacy)
    head = git(legacy, "rev-parse", "HEAD").stdout
    report = migrate(legacy)
    assert report.exit_code == 1
    assert "collision" in str(report.lines())
    assert snapshot(legacy) == before
    assert git(legacy, "rev-parse", "HEAD").stdout == head
    assert git(legacy, "status", "--porcelain").stdout == ""


def test_dirty_repository_is_rejected_without_losing_changes(legacy):
    write(legacy / "instructions/global/base.md", "Uncommitted operator changes\n")
    write(legacy / "untracked.md", "Untracked content\n")
    before = snapshot(legacy)
    report = migrate(legacy)
    assert report.exit_code == 1
    assert "uncommitted" in str(report.lines())
    assert snapshot(legacy) == before


def test_valid_new_settings_and_filters_are_preserved(legacy):
    write(
        legacy / "hub.toml",
        '[agents]\nenabled = ["codex"]\nmode = "copy"\n\n[skills.existing]\nagents = ["codex"]\n',
    )
    git(legacy, "add", "hub.toml")
    git(legacy, "commit", "-m", "new settings")
    assert_ok(migrate(legacy))
    data = tomllib.loads((legacy / "hub.toml").read_text())
    assert data["agents"] == {"enabled": ["codex"], "mode": "copy"}
    assert data["skills"]["existing"] == {"agents": ["codex"]}
    assert data["skills"]["alpha"]["agents"] == ["claude-code"]


def test_conflicting_new_and_old_filters_stop_before_mutation(legacy):
    write(legacy / "hub.toml", '[skills.alpha]\nagents = ["codex"]\n')
    git(legacy, "add", "hub.toml")
    git(legacy, "commit", "-m", "conflicting filter")
    before = snapshot(legacy)
    report = migrate(legacy)
    assert report.exit_code == 1
    assert "configuration collision" in str(report.lines())
    assert snapshot(legacy) == before


def test_root_level_legacy_configs_are_supported(legacy):
    for path in (legacy / "config").glob("*.toml"):
        git(legacy, "mv", str(path.relative_to(legacy)), path.name)
    git(legacy, "commit", "-m", "root configuration")
    assert_ok(migrate(legacy))
    assert "machines" not in tomllib.loads((legacy / "hub.toml").read_text())
    assert not (legacy / "projects.toml").exists()
    assert (
        legacy / "projects/example.test--owner--project/skills/beta/SKILL.md"
    ).exists()


def test_failed_commit_restores_original_files_and_index(legacy, monkeypatch):
    before = snapshot(legacy)
    old_head = git(legacy, "rev-parse", "HEAD").stdout
    original = gitio.run_git

    def reject_commit(repo: Path, *args, **kwargs):
        if args and args[0] == "commit":
            raise gitio.GitCommandError("test commit rejected")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(gitio, "run_git", reject_commit)
    report = migrate(legacy)
    assert report.exit_code == 1
    assert "test commit rejected" in str(report.lines())
    assert snapshot(legacy) == before
    assert git(legacy, "rev-parse", "HEAD").stdout == old_head
    assert git(legacy, "status", "--porcelain").stdout == ""


def test_rejected_formatting_hook_restores_original_tracked_content(legacy):
    before = snapshot(legacy)
    old_head = git(legacy, "rev-parse", "HEAD").stdout
    hook = legacy / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "printf 'Changed by formatter\n' > AGENTS.md\n"
        "printf 'Changed skill\n' > skills/alpha/SKILL.md\n"
        "chmod +x skills/alpha/SKILL.md\n"
        "git add AGENTS.md skills/alpha/SKILL.md\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    report = migrate(legacy)

    assert report.exit_code == 1
    assert "git commit exited with code 1" in str(report.lines())
    assert snapshot(legacy) == before
    assert git(legacy, "rev-parse", "HEAD").stdout == old_head
    assert git(legacy, "status", "--porcelain").stdout == ""
    assert not (legacy / "skills/global/alpha/SKILL.md").stat().st_mode & 0o111


def test_symlink_destination_is_not_followed(legacy, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside / "keep.md", "external\n")
    (legacy / "agents").symlink_to(outside, target_is_directory=True)
    git(legacy, "add", "agents")
    git(legacy, "commit", "-m", "external link")
    report = migrate(legacy)
    assert report.exit_code == 1
    assert "symlink" in str(report.lines())
    assert not (outside / "claude-code.md").exists()
    assert (outside / "keep.md").read_text() == "external\n"
