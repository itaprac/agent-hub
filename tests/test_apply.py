"""Structured apply: deployment, drift protection, pruning, and the skill rule."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agenthub import config, core, operations

from conftest import AGENTS_TOML, MACHINE_ID, write


def apply(repo: Path, dry_run: bool = False) -> core.ApplyReport:
    return core.apply_report(config.load_machine_projection(repo), dry_run=dry_run)


def by_text(report: core.ApplyReport) -> dict[str, str]:
    return {check.text: check.level for check in report.checks}


def find(report: core.Report, target: Path) -> core.StatusCheck:
    return next(check for check in report.checks if check.target == str(target))


# ------------------------------------------------------------------ deployment

def test_fresh_apply_deploys_symlinks_and_instructions(
    content: Path, home: Path, project: Path
) -> None:
    report = apply(content)
    assert report.exit_code == 0
    alpha = home / ".claude" / "skills" / "alpha"
    beta = project / ".claude" / "skills" / "beta"
    assert alpha.is_symlink() and alpha.resolve() == content / "skills" / "global" / "alpha"
    assert beta.is_symlink() and beta.resolve() == content / "skills" / "projects" / "demo" / "beta"
    rendered = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert core.BEGIN_MARKER in rendered
    assert core.MANAGED_NOTICE in rendered
    assert "Global base" in rendered


def test_fresh_apply_reports_each_action(content: Path, home: Path, project: Path) -> None:
    levels = by_text(apply(content))
    source = content / "skills" / "global" / "alpha"
    assert levels[f"claude global/alpha: {home}/.claude/skills/alpha -> {source}"] == "link"
    assert levels[f"claude global: {home}/.claude/CLAUDE.md"] == "render"
    assert levels[f"project absent: no path for machine '{MACHINE_ID}'"] == "skip"


def test_second_apply_is_idempotent(content: Path) -> None:
    apply(content)
    report = apply(content)
    assert report.exit_code == 0
    assert {check.level for check in report.checks} == {"ok", "skip"}


def test_dry_run_changes_nothing(content: Path, home: Path) -> None:
    report = apply(content, dry_run=True)
    assert report.exit_code == 0
    assert report.dry_run is True
    assert {check.level for check in report.checks} == {"link", "render", "skip"}
    assert not (home / ".claude" / "skills" / "alpha").is_symlink()
    assert not (home / ".claude" / "CLAUDE.md").exists()


def test_report_identifies_the_machine_and_the_repository(content: Path) -> None:
    report = apply(content, dry_run=True)
    assert report.machine_id == MACHINE_ID
    assert report.repo == str(content)


def test_to_dict_keeps_the_run_contract(content: Path) -> None:
    payload = apply(content, dry_run=True).to_dict()
    assert payload["command"] == "--dry-run apply"
    assert payload["dry_run"] is True
    assert payload["exit_code"] == 0
    assert all(set(line) == {"level", "text"} for line in payload["lines"])
    assert apply(content).to_dict()["command"] == "apply"


# ----------------------------------------------------------------------- drift

def test_regular_directory_target_is_drift(content: Path, home: Path) -> None:
    apply(content)
    target = home / ".claude" / "skills" / "alpha"
    target.unlink()
    (target / "keep").mkdir(parents=True)
    report = apply(content)
    check = find(report, target)
    assert check.level == "DRIFT"
    assert check.text == f"claude global/alpha: {target} is not a symlink; run: hub adopt {target}"
    assert report.exit_code == 1
    assert (target / "keep").is_dir()


def test_foreign_symlink_is_replaced(content: Path, home: Path, tmp_path: Path) -> None:
    apply(content)
    target = home / ".claude" / "skills" / "alpha"
    target.unlink()
    target.symlink_to(tmp_path, target_is_directory=True)
    source = content / "skills" / "global" / "alpha"
    check = find(apply(content), target)
    assert check.level == "link"
    assert check.text == f"replace claude global/alpha: {target} -> {source}"
    assert target.resolve() == source


def test_instruction_directory_target_is_drift(content: Path, home: Path) -> None:
    target = home / ".claude" / "CLAUDE.md"
    target.mkdir()
    report = apply(content)
    check = find(report, target)
    assert check.level == "DRIFT"
    assert check.text.endswith("is not a regular file")
    assert target.is_dir()


def test_instruction_broken_symlink_is_drift(content: Path, home: Path) -> None:
    target = home / ".claude" / "CLAUDE.md"
    target.symlink_to(home / "missing.md")
    check = find(apply(content), target)
    assert check.level == "DRIFT"
    assert check.text.endswith("is a broken symlink")
    assert target.is_symlink()


# ---------------------------------------------------------------- managed block

def test_user_content_outside_the_block_is_preserved(content: Path, home: Path) -> None:
    target = home / ".claude" / "CLAUDE.md"
    target.write_text("My notes\n", encoding="utf-8")
    apply(content)
    rendered = target.read_text(encoding="utf-8")
    assert rendered.startswith("My notes\n")
    assert core.BEGIN_MARKER in rendered


def test_retired_polish_marker_is_drift_and_untouched(content: Path, home: Path) -> None:
    target = home / ".claude" / "CLAUDE.md"
    retired_marker = (
        "<!-- agent-hub:begin (managed by agent-hub; "
        + "edytuj "
        + "w repo, nie tutaj) -->"
    )
    original = f"Before\n\n{retired_marker}\nold\n{core.END_MARKER}\n\nAfter\n"
    target.write_text(original, encoding="utf-8")

    status = operations.ContentOperations(content).status()
    status_check = find(status, target)
    assert status_check.level == "STALE"
    assert status_check.text.endswith("has missing or malformed managed markers")
    assert status.exit_code == 1

    report = apply(content)
    apply_check = find(report, target)
    assert apply_check.level == "DRIFT"
    assert apply_check.text.endswith("has malformed or duplicate managed markers")
    assert report.exit_code == 1
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "malformed",
    [
        f"{core.BEGIN_MARKER}\nx\n",
        f"x\n{core.END_MARKER}\n",
        (
            f"{core.BEGIN_MARKER}\none\n{core.END_MARKER}\n"
            f"{core.BEGIN_MARKER}\ntwo\n{core.END_MARKER}\n"
        ),
    ],
    ids=["missing-end", "missing-begin", "multiple-blocks"],
)
def test_malformed_markers_are_drift_and_untouched(
    content: Path, home: Path, malformed: str
) -> None:
    target = home / ".claude" / "CLAUDE.md"
    target.write_text(malformed, encoding="utf-8")

    status = operations.ContentOperations(content).status()
    status_check = find(status, target)
    assert status_check.level == "STALE"
    assert status_check.text.endswith("has missing or malformed managed markers")
    assert status.exit_code == 1

    report = apply(content)
    check = find(report, target)
    assert check.level == "DRIFT"
    assert check.text.endswith("has malformed or duplicate managed markers")
    assert report.exit_code == 1
    assert target.read_text(encoding="utf-8") == malformed


def test_app_files_do_not_contain_the_retired_polish_marker_text() -> None:
    repo = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    excluded = {".git", "skills", "instructions", "config"}
    marker_text = b"edytuj " + b"w repo"
    matches = []
    for raw_path in tracked:
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        if relative.parts[0] in excluded:
            continue
        path = repo / relative
        if path.is_symlink():
            contents = os.fsencode(os.readlink(path))
        elif path.is_file():
            contents = path.read_bytes()
        else:
            continue
        if marker_text in contents:
            matches.append(str(relative))
    assert matches == []


# --------------------------------------------------------------------- pruning

def test_orphaned_repository_link_is_pruned(content: Path, home: Path, tmp_path: Path) -> None:
    apply(content)
    link = home / ".claude" / "skills" / "alpha"
    shutil.move(str(content / "skills" / "global" / "alpha"), str(tmp_path / "removed"))
    report = apply(content)
    pruned = [check for check in report.checks if check.kind == "prune"]
    assert len(pruned) == 1
    assert pruned[0].level == "prune"
    assert pruned[0].text.startswith(f"remove {link} -> ")
    assert not link.is_symlink()


def test_dry_run_keeps_the_orphaned_link(content: Path, home: Path, tmp_path: Path) -> None:
    apply(content)
    link = home / ".claude" / "skills" / "alpha"
    shutil.move(str(content / "skills" / "global" / "alpha"), str(tmp_path / "removed"))
    report = apply(content, dry_run=True)
    pruned = [check for check in report.checks if check.kind == "prune"]
    assert pruned[0].text.startswith(f"would remove {link} -> ")
    assert link.is_symlink()


def test_foreign_links_are_never_pruned(content: Path, home: Path, tmp_path: Path) -> None:
    apply(content)
    foreign = home / ".claude" / "skills" / "elsewhere"
    foreign.symlink_to(tmp_path, target_is_directory=True)
    report = apply(content)
    assert not [check for check in report.checks if check.kind == "prune"]
    assert foreign.is_symlink()


# ------------------------------------------------------------------- copy mode

def copy_mode(content: Path) -> None:
    write(content / "config" / "agents.toml", AGENTS_TOML.replace('"symlink"', '"copy"'))


def test_copy_mode_deploys_a_real_directory(content: Path, home: Path) -> None:
    copy_mode(content)
    report = apply(content)
    target = home / ".claude" / "skills" / "alpha"
    assert report.exit_code == 0
    assert target.is_dir() and not target.is_symlink()
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# alpha\n"


def test_copy_mode_repairs_content_and_removes_extras(content: Path, home: Path) -> None:
    copy_mode(content)
    apply(content)
    target = home / ".claude" / "skills" / "alpha"
    (target / "SKILL.md").write_text("edited\n", encoding="utf-8")
    (target / "extra.md").write_text("extra\n", encoding="utf-8")
    check = find(apply(content), target)
    assert check.level == "copy"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# alpha\n"
    assert not (target / "extra.md").exists()


def test_copy_over_a_file_is_drift(content: Path, home: Path) -> None:
    copy_mode(content)
    target = home / ".claude" / "skills" / "alpha"
    target.parent.mkdir(parents=True)
    target.write_text("a file\n", encoding="utf-8")
    report = apply(content)
    check = find(report, target)
    assert check.level == "DRIFT"
    assert check.text.endswith("cannot be copied over a non-directory target")
    assert target.read_text(encoding="utf-8") == "a file\n"


# ----------------------------------------------------------- skill directories

def test_hidden_and_empty_directories_are_not_skills(content: Path, home: Path) -> None:
    skills = content / "skills" / "global"
    write(skills / ".hidden" / "SKILL.md", "# hidden\n")
    (skills / "empty").mkdir()
    write(skills / "dotfiles-only" / ".secret", "hidden file\n")
    assert [path.name for path in config.skill_directories(skills)] == ["alpha"]
    apply(content)
    deployed = sorted(path.name for path in (home / ".claude" / "skills").iterdir())
    assert deployed == ["alpha"]


def test_skill_directories_sort_case_insensitively(content: Path) -> None:
    skills = content / "skills" / "global"
    write(skills / "Bravo" / "SKILL.md", "# Bravo\n")
    write(skills / "charlie" / "SKILL.md", "# charlie\n")
    assert [path.name for path in config.skill_directories(skills)] == [
        "alpha",
        "Bravo",
        "charlie",
    ]
    names = [check.name for check in apply(content).checks if check.kind == "skill"]
    assert names[:3] == ["alpha", "Bravo", "charlie"]
