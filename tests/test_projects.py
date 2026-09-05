"""Private Project links and Git exclusions never change tracked files."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agenthub import config, projects
from conftest import git, write


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "project tests")
    git(root, "config", "user.email", "project@example.invalid")
    write(root / "AGENTS.md", "Committed project notes\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "project")
    git(root, "remote", "add", "origin", "git@github.com:Owner/Repo.git")
    return root.resolve()


@pytest.fixture
def project_skill(content: Path, checkout: Path) -> Path:
    path = content / "projects" / projects.project_slug(checkout) / "skills" / "private"
    write(path / "SKILL.md", "Private skill\n")
    return path


@pytest.mark.parametrize("origin", [
    "https://github.com/Owner/Repo.git",
    "https://user:password@github.com/Owner/Repo.git/",
    "ssh://git@github.com/Owner/Repo.git",
    "git@github.com:Owner/Repo.git",
    "https://GITHUB.COM/owner/repo",
    "ssh://git@github.com:22/owner/repo.git",
])
def test_equivalent_origins_have_the_same_slug(origin: str) -> None:
    assert projects.slug_from_url(origin) == "github.com--owner--repo"


@pytest.mark.parametrize("origin", ["", "https://github.com/../repo", "git@github.com:../repo", "https://github.com/repo?token=x", "/tmp/repo\nother"])
def test_unsafe_origin_does_not_become_a_store_path(origin: str) -> None:
    with pytest.raises(ValueError):
        projects.slug_from_url(origin)


def test_file_and_absolute_local_origins_have_the_same_slug() -> None:
    assert projects.slug_from_url("file:///tmp/Origin.git") == projects.slug_from_url("/tmp/Origin.git")


def test_link_registers_locally_and_keeps_the_checkout_clean(content: Path, checkout: Path, home: Path, project_skill: Path) -> None:
    before = (checkout / "AGENTS.md").read_bytes()
    report = projects.link_project(content, checkout)
    assert report.exit_code == 0, report.lines()
    assert report.command == "project link"
    slug = projects.project_slug(checkout)
    assert projects.load_projects() == {slug: checkout}
    registry = home / ".config" / "agent-hub" / "projects.json"
    assert registry.stat().st_mode & 0o777 == 0o600
    assert not (content / "projects.json").exists()
    for folder in (".agents", ".claude"):
        link = checkout / folder / "skills" / "private"
        assert link.is_symlink() and link.resolve() == project_skill
        assert not os.path.isabs(os.readlink(link))
    assert git(checkout, "status", "--porcelain").stdout == ""
    assert (checkout / "AGENTS.md").read_bytes() == before
    before_registry = registry.stat().st_mtime_ns
    excluded = checkout / ".git" / "info" / "exclude"
    before_exclude = excluded.stat().st_mtime_ns
    assert projects.link_project(content, checkout).exit_code == 0
    assert registry.stat().st_mtime_ns == before_registry
    assert excluded.stat().st_mtime_ns == before_exclude


def test_linked_worktree_uses_the_common_git_exclude(content: Path, checkout: Path, project_skill: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    git(checkout, "worktree", "add", "-b", "other", str(linked))
    assert (linked / ".git").is_file()
    assert projects.project_root(linked) == linked.resolve()
    report = projects.link_project(content, linked)
    assert report.exit_code == 0, report.lines()
    assert (linked / ".agents" / "skills" / "private").resolve() == project_skill
    assert git(linked, "status", "--porcelain").stdout == ""
    assert "/.agents/skills/private" in (checkout / ".git" / "info" / "exclude").read_text()


def test_registry_rejects_relative_and_malformed_values(home: Path) -> None:
    registry = home / ".config" / "agent-hub" / "projects.json"
    for document in (["wrong shape"], {"../escape": "/tmp/root"}, {"slug": "relative"}, {"slug": 42}, {"slug": "/tmp/../outside"}):
        registry.write_text(json.dumps(document))
        with pytest.raises(ValueError):
            projects.load_projects()


def test_registry_symlink_is_never_followed(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "registry.json"
    outside.write_text("{}\n")
    (home / ".config" / "agent-hub" / "projects.json").symlink_to(outside)
    with pytest.raises(ValueError):
        projects.load_projects()
    assert outside.read_text() == "{}\n"


@pytest.mark.parametrize("folder", [".agents", ".claude"])
def test_symlink_parent_cannot_redirect_project_links(content: Path, checkout: Path, project_skill: Path, tmp_path: Path, folder: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / folder).symlink_to(outside, target_is_directory=True)
    report = projects.link_project(content, checkout)
    assert report.exit_code == 1
    assert not list(outside.iterdir())
    assert any(check.level == "DRIFT" and "parent" in check.text for check in report.checks)


def test_symlink_exclude_file_stops_before_creating_links(content: Path, checkout: Path, project_skill: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("do not edit\n")
    exclude = checkout / ".git" / "info" / "exclude"
    exclude.unlink()
    exclude.symlink_to(outside)
    report = projects.link_project(content, checkout)
    assert report.exit_code == 1
    assert outside.read_text() == "do not edit\n"
    assert not (checkout / ".agents" / "skills").exists()


@pytest.mark.parametrize("kind", ["directory", "foreign-link", "tracked-link"])
def test_existing_project_paths_are_not_overwritten(content: Path, checkout: Path, project_skill: Path, tmp_path: Path, kind: str) -> None:
    target = checkout / ".agents" / "skills" / "private"
    target.parent.mkdir(parents=True)
    if kind == "directory":
        write(target / "keep.md", "keep\n")
    elif kind == "foreign-link":
        target.symlink_to(tmp_path, target_is_directory=True)
    else:
        target.symlink_to(project_skill, target_is_directory=True)
        git(checkout, "add", ".agents/skills/private")
        git(checkout, "commit", "-qm", "tracked link")
    before = os.readlink(target) if target.is_symlink() else (target / "keep.md").read_text()
    report = projects.link_project(content, checkout)
    assert report.exit_code == 1
    after = os.readlink(target) if target.is_symlink() else (target / "keep.md").read_text()
    assert after == before


def test_stale_links_are_reported_and_pruned_without_touching_foreign_paths(content: Path, checkout: Path, project_skill: Path, tmp_path: Path) -> None:
    assert projects.link_project(content, checkout).exit_code == 0
    foreign = checkout / ".agents" / "skills" / "foreign"
    foreign.symlink_to(tmp_path)
    shutil.rmtree(project_skill)
    projection = config.load_machine_projection(content)
    stale = projects.check_links(projection)
    assert len([check for check in stale if check.level == "STALE"]) == 2
    projects.apply_links(projection, dry_run=True)
    assert (checkout / ".agents" / "skills" / "private").is_symlink()
    projects.apply_links(projection)
    assert not (checkout / ".agents" / "skills" / "private").is_symlink()
    assert foreign.is_symlink()


def test_project_filters_select_machine_and_agent_targets(content: Path, checkout: Path, project_skill: Path) -> None:
    hub = content / "hub.toml"
    hub.write_text(hub.read_text() + '\n[skills.private]\nmachines=["other-machine"]\n')
    assert projects.link_project(content, checkout).exit_code == 0
    assert not (checkout / ".agents" / "skills" / "private").exists()
    hub.write_text(hub.read_text().replace('machines=["other-machine"]', 'agents=["claude"]'))
    assert projects.link_project(content, checkout).exit_code == 0
    assert (checkout / ".agents" / "skills" / "private").is_symlink()
    assert (checkout / ".claude" / "skills" / "private").is_symlink()


def test_project_links_use_the_settings_loaded_for_the_operation(content: Path, checkout: Path, project_skill: Path) -> None:
    assert projects.link_project(content, checkout).exit_code == 0
    projection = config.load_machine_projection(content)
    hub = content / "hub.toml"
    hub.write_text(hub.read_text() + '\n[skills.private]\nmachines=["other-machine"]\n')

    projects.apply_links(projection)
    for folder in (".agents", ".claude"):
        assert (checkout / folder / "skills" / "private").resolve() == project_skill

    projects.apply_links(config.load_machine_projection(content))
    for folder in (".agents", ".claude"):
        assert not (checkout / folder / "skills" / "private").is_symlink()


def test_changed_origin_does_not_deploy_another_projects_skills(content: Path, checkout: Path, project_skill: Path) -> None:
    assert projects.link_project(content, checkout).exit_code == 0
    link = checkout / ".agents" / "skills" / "private"
    link.unlink()
    git(checkout, "remote", "set-url", "origin", "https://github.com/other/project")
    checks = projects.apply_links(config.load_machine_projection(content))
    assert any(check.level == "DRIFT" and "origin" in check.text for check in checks)
    assert not link.exists()


@pytest.mark.parametrize("original", [b"# custom notes\r\n*.tmp\r\n", b"# legacy \xff\r\n*.tmp\r\n"])
def test_exclude_preserves_existing_bytes_and_escapes_literal_paths(checkout: Path, original: bytes) -> None:
    exclude = checkout / ".git" / "info" / "exclude"
    exclude.write_bytes(original)
    path = checkout / "custom [skill]" / "with spaces"
    write(path / "SKILL.md", "private\n")
    projects.exclude_paths(checkout, [path])
    assert exclude.read_bytes().startswith(original)
    assert git(checkout, "status", "--porcelain").stdout == ""
    changed = exclude.stat().st_mtime_ns
    assert projects.exclude_paths(checkout, [path]) == []
    assert exclude.stat().st_mtime_ns == changed
