"""Adoption preserves link targets and restores original links after a failure."""

import os
from pathlib import Path

import pytest

from agenthub import config, core, projects
from conftest import git, write


def skill_source(home: Path, tmp_path: Path, project: bool) -> Path:
    if project:
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        git(checkout, "init", "-b", "main")
        git(
            checkout,
            "remote",
            "add",
            "origin",
            "https://example.test/owner/project.git",
        )
        source = checkout / ".claude/skills/linked-skill"
    else:
        source = home / "incoming/linked-skill"
    write(source / "SKILL.md", "# Skill with linked resources\n")
    return source


def adopt(content: Path, source: Path, project: bool):
    return core.adopt_skill_report(
        config.load_machine_projection(content), str(source), project, None
    )


def destination(content: Path, source: Path, project: bool) -> Path:
    if project:
        return (
            content
            / "projects"
            / projects.project_slug(source)
            / "skills"
            / source.name
        )
    return content / "skills" / source.name


@pytest.mark.parametrize("project", [False, True])
def test_adopt_preserves_external_and_dangling_relative_targets(
    content, home, tmp_path, project
):
    source = skill_source(home, tmp_path, project)
    resource = source.parent / "resource.txt"
    resource.write_text("external resource\n")
    folder = source.parent / "external-directory"
    write(folder / "asset.txt", "directory resource\n")
    missing = source.parent / "missing.txt"
    (source / "external.txt").symlink_to("../resource.txt")
    (source / "external-dir").symlink_to(
        "../external-directory", target_is_directory=True
    )
    (source / "dangling.txt").symlink_to("../missing.txt")
    target = destination(content, source, project)

    report = adopt(content, source, project)

    assert report.exit_code == 0, report.lines()
    assert source.is_symlink() and source.resolve() == target
    assert (target / "external.txt").resolve() == resource
    assert (target / "external.txt").read_text() == "external resource\n"
    assert (target / "external-dir").resolve() == folder
    assert (target / "external-dir/asset.txt").read_text() == "directory resource\n"
    assert (target / "dangling.txt").is_symlink()
    assert (target / "dangling.txt").resolve(strict=False) == missing
    assert not os.path.isabs(os.readlink(target / "external.txt"))
    assert not list(target.parent.glob(".agent-hub-adopt-*"))


@pytest.mark.parametrize("project", [False, True])
def test_adopt_keeps_internal_link_text_and_absolute_targets(
    content, home, tmp_path, project
):
    source = skill_source(home, tmp_path, project)
    write(source / "data/asset.txt", "internal resource\n")
    (source / "internal").symlink_to("./data/../data/asset.txt")
    (source / "internal-directory").symlink_to("data", target_is_directory=True)
    external = home / "absolute-resource.txt"
    external.write_text("absolute resource\n")
    (source / "absolute").symlink_to(external)
    target = destination(content, source, project)

    report = adopt(content, source, project)

    assert report.exit_code == 0, report.lines()
    assert os.readlink(target / "internal") == "./data/../data/asset.txt"
    assert os.readlink(target / "internal-directory") == "data"
    assert (target / "internal").read_text() == "internal resource\n"
    assert (
        target / "internal-directory/asset.txt"
    ).read_text() == "internal resource\n"
    assert os.readlink(target / "absolute") == str(external)


@pytest.mark.parametrize("project", [False, True])
def test_failed_replacement_restores_source_and_original_link_text(
    content, home, tmp_path, monkeypatch, project
):
    source = skill_source(home, tmp_path, project)
    write(source.parent / "resource.txt", "external\n")
    (source / "external").symlink_to("../resource.txt")
    (source / "dangling").symlink_to("../missing.txt")
    target = destination(content, source, project)
    original = Path.symlink_to

    def reject_replacement(self, value, target_is_directory=False):
        if self == source:
            raise OSError("replacement link denied")
        return original(self, value, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", reject_replacement)
    report = adopt(content, source, project)

    assert report.exit_code == 1
    assert "replacement link denied" in str(report.lines())
    assert source.is_dir() and not source.is_symlink()
    assert os.readlink(source / "external") == "../resource.txt"
    assert os.readlink(source / "dangling") == "../missing.txt"
    assert (source / "external").read_text() == "external\n"
    assert not target.exists()
    assert not list(target.parent.glob(".agent-hub-adopt-*"))
    if project:
        assert not projects.load_projects()


def test_failed_rewrite_restores_all_links_without_creating_new_symlinks(
    content, home, tmp_path, monkeypatch
):
    source = skill_source(home, tmp_path, False)
    for name in ("first", "second"):
        write(source.parent / f"{name}.txt", name)
        (source / name).symlink_to(f"../{name}.txt")
    target = destination(content, source, False)
    original = Path.symlink_to
    calls = []

    def reject_second_rewrite(self, value, target_is_directory=False):
        calls.append(self)
        if len(calls) >= 2:
            raise OSError("rewrite denied")
        return original(self, value, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", reject_second_rewrite)
    report = adopt(content, source, False)

    assert report.exit_code == 1
    assert "rewrite denied" in str(report.lines())
    assert source.is_dir() and not source.is_symlink()
    assert os.readlink(source / "first") == "../first.txt"
    assert os.readlink(source / "second") == "../second.txt"
    assert (source / "first").read_text() == "first"
    assert (source / "second").read_text() == "second"
    assert len(calls) == 2
    assert not target.exists()
    assert not list(target.parent.glob(".agent-hub-adopt-*"))
