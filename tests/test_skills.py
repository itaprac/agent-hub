"""Package contract: skill creation and adoption return structured reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenthub import config, core

SKILL_TEMPLATE = (
    '---\nname: gamma\ndescription: "TODO: describe when to use this skill."\n---\n\n# gamma\n'
)


def context(content: Path) -> dict[str, Any]:
    return config.load_context(content)


# ------------------------------------------------------------------- add-skill

def test_add_skill_creates_the_minimal_global_template(content: Path, home: Path) -> None:
    report = core.add_skill_report(context(content), "gamma", None)
    assert report.command == "add-skill"
    assert report.exit_code == 0
    skill_file = content / "skills" / "global" / "gamma" / "SKILL.md"
    assert skill_file.read_text(encoding="utf-8") == SKILL_TEMPLATE
    [check] = report.checks
    assert check.level == "ok"
    assert check.kind == "skill"
    assert check.name == "gamma"
    assert str(skill_file) in check.text


def test_add_skill_creates_a_project_skill(content: Path, home: Path) -> None:
    report = core.add_skill_report(context(content), "gamma", "demo")
    assert report.exit_code == 0
    assert (content / "skills" / "projects" / "demo" / "gamma" / "SKILL.md").is_file()
    assert report.checks[0].project == "demo"


def test_add_skill_rejects_an_unknown_project(content: Path, home: Path) -> None:
    report = core.add_skill_report(context(content), "gamma", "nope")
    assert report.exit_code == 1
    [check] = report.checks
    assert check.level == "ERROR"
    assert "nope" in check.text
    assert not (content / "skills" / "projects" / "nope").exists()


def test_add_skill_rejects_a_duplicate(content: Path, home: Path) -> None:
    report = core.add_skill_report(context(content), "alpha", None)
    assert report.exit_code == 1
    [check] = report.checks
    assert check.level == "ERROR"
    assert "already exists" in check.text
    # The existing skill is untouched.
    assert (content / "skills" / "global" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# alpha\n"


def test_add_skill_rejects_an_invalid_name(content: Path, home: Path) -> None:
    report = core.add_skill_report(context(content), "../escape", None)
    assert report.exit_code == 1
    assert report.checks[0].level == "ERROR"
    assert "invalid skill name" in report.checks[0].text


# ----------------------------------------------------------------------- adopt

def make_source(home: Path, name: str = "local-skill") -> Path:
    source = home / name
    source.mkdir()
    (source / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return source


def test_adopt_moves_the_skill_and_links_back(content: Path, home: Path) -> None:
    source = make_source(home)
    report = core.adopt_skill_report(context(content), str(source), None, None)
    assert report.command == "adopt"
    assert report.exit_code == 0
    destination = content / "skills" / "global" / "local-skill"
    assert (destination / "SKILL.md").is_file()
    assert source.is_symlink()
    assert source.resolve() == destination.resolve()
    levels = [check.level for check in report.checks]
    assert levels == ["ok", "ok"]
    assert "adopted" in report.checks[0].text
    assert "hub apply" in report.checks[1].text


def test_adopt_uses_the_explicit_name_and_project(content: Path, home: Path) -> None:
    source = make_source(home)
    report = core.adopt_skill_report(context(content), str(source), "demo", "renamed")
    assert report.exit_code == 0
    assert (content / "skills" / "projects" / "demo" / "renamed" / "SKILL.md").is_file()


def test_adopt_rejects_a_missing_or_non_directory_source(content: Path, home: Path) -> None:
    ctx = context(content)
    missing = core.adopt_skill_report(ctx, str(home / "absent"), None, None)
    assert missing.exit_code == 1
    assert missing.checks[0].level == "ERROR"

    file_path = home / "file.md"
    file_path.write_text("not a directory\n", encoding="utf-8")
    as_file = core.adopt_skill_report(ctx, str(file_path), None, None)
    assert as_file.exit_code == 1

    link = home / "linked"
    link.symlink_to(home)
    as_symlink = core.adopt_skill_report(ctx, str(link), None, None)
    assert as_symlink.exit_code == 1


def test_adopt_refuses_a_destination_collision(content: Path, home: Path) -> None:
    source = make_source(home, "alpha")
    report = core.adopt_skill_report(context(content), str(source), None, None)
    assert report.exit_code == 1
    [check] = report.checks
    assert check.level == "ERROR"
    assert "already exists" in check.text
    # The source stays where it was; nothing moved.
    assert source.is_dir() and not source.is_symlink()
    assert (source / "SKILL.md").is_file()


def test_adopt_rejects_an_unknown_project(content: Path, home: Path) -> None:
    source = make_source(home)
    report = core.adopt_skill_report(context(content), str(source), "nope", None)
    assert report.exit_code == 1
    assert source.is_dir() and not source.is_symlink()


# --------------------------------------------------------------------- listing

def test_skill_directories_lists_visible_non_empty_case_insensitive(
    content: Path, home: Path
) -> None:
    parent = content / "skills" / "global"
    (parent / "empty").mkdir()
    (parent / ".hidden").mkdir()
    (parent / ".hidden" / "SKILL.md").write_text("# hidden\n", encoding="utf-8")
    (parent / "dotted").mkdir()
    (parent / "dotted" / ".only-hidden").write_text("hidden\n", encoding="utf-8")
    (parent / "Bravo").mkdir()
    (parent / "Bravo" / "SKILL.md").write_text("# Bravo\n", encoding="utf-8")
    names = [directory.name for directory in core.skill_directories(parent)]
    assert names == ["alpha", "Bravo"]
