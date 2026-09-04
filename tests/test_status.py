"""Structured status: machine resolution, managed targets, and git state."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agenthub import core, operations

from conftest import MACHINE_ID


def apply(repo: Path) -> None:
    operations.ContentOperations(repo).apply()


def status(repo: Path) -> core.StatusReport:
    return operations.ContentOperations(repo).status()


def by_text(report: core.StatusReport) -> dict[str, str]:
    return {check.text: check.level for check in report.checks}


def find(report: core.StatusReport, target: Path) -> core.StatusCheck:
    return next(check for check in report.checks if check.target == str(target))


def test_report_identifies_the_machine_and_the_repository(content: Path) -> None:
    report = status(content)
    assert report.machine_id == MACHINE_ID
    assert report.repo == str(content)


def test_fresh_repository_reports_every_target_as_missing(
    content: Path, home: Path, project: Path
) -> None:
    report = status(content)
    levels = by_text(report)
    assert levels[f"claude global/alpha: {home}/.claude/skills/alpha"] == "MISSING"
    assert levels[f"claude global: {home}/.claude/CLAUDE.md"] == "MISSING"
    assert report.exit_code == 1
    assert report.problems == 2


def test_applied_repository_is_clean(content: Path) -> None:
    apply(content)
    report = status(content)
    assert report.exit_code == 0
    assert report.problems == 0
    assert {check.level for check in report.checks} == {"ok", "skip"}


def test_replaced_skill_symlink_is_drift(content: Path, home: Path) -> None:
    apply(content)
    target = home / ".claude" / "skills" / "alpha"
    target.unlink()
    target.mkdir()
    report = status(content)
    check = find(report, target)
    assert check.level == "DRIFT"
    assert check.text == f"claude global/alpha: {target} is not a symlink"
    assert report.exit_code == 1


def test_edited_managed_block_is_stale(content: Path, home: Path) -> None:
    apply(content)
    target = home / ".claude" / "CLAUDE.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("Global base", "edited"), encoding="utf-8"
    )
    check = find(status(content), target)
    assert check.level == "STALE"
    assert check.text.endswith("managed content is out of date")


def test_removed_managed_markers_are_stale(content: Path, home: Path) -> None:
    apply(content)
    target = home / ".claude" / "CLAUDE.md"
    target.write_text("no markers here\n", encoding="utf-8")
    check = find(status(content), target)
    assert check.level == "STALE"
    assert check.text.endswith("has missing or malformed managed markers")


def test_orphaned_skill_symlink_is_stale(content: Path, home: Path) -> None:
    apply(content)
    orphan = home / ".claude" / "skills" / "removed"
    orphan.symlink_to(content / "skills" / "global" / "removed", target_is_directory=True)
    report = status(content)
    orphaned = [check for check in report.checks if check.kind == "orphan"]
    assert len(orphaned) == 1
    assert orphaned[0].level == "STALE"
    assert str(orphan) in orphaned[0].text


def test_clean_worktree_and_missing_upstream(content: Path) -> None:
    report = status(content)
    git = [check for check in report.checks if check.kind == "git"]
    assert [check.level for check in git] == ["ok", "skip"]
    assert git[0].text == "git: working tree clean"
    assert git[1].text == "git: no upstream configured"


def test_uncommitted_changes_are_drift(content: Path) -> None:
    (content / "skills" / "global" / "alpha" / "SKILL.md").write_text("# changed\n", encoding="utf-8")
    report = status(content)
    git = [check for check in report.checks if check.kind == "git"]
    assert git[0].level == "DRIFT"
    assert "1 uncommitted change(s)" in git[0].text


def test_repository_without_git_reports_an_error(content: Path) -> None:
    shutil.rmtree(content / ".git")
    report = status(content)
    git = [check for check in report.checks if check.kind == "git"]
    assert [check.level for check in git] == ["ERROR"]
    assert report.exit_code == 1


def test_git_checks_come_last(content: Path) -> None:
    report = status(content)
    assert report.checks[-1].kind == "git"


def test_checks_carry_target_metadata(content: Path, home: Path) -> None:
    report = status(content)
    alpha = next(check for check in report.checks if check.name == "alpha")
    assert alpha.kind == "skill"
    assert alpha.agent == "claude"
    assert alpha.project is None
    assert alpha.target == str(home / ".claude" / "skills" / "alpha")


def test_lines_render_the_command_output(content: Path) -> None:
    report = status(content)
    lines = report.lines()
    assert len(lines) == len(report.checks)
    assert lines[0] == {"level": report.checks[0].level, "text": report.checks[0].text}


def test_status_rejects_an_invalid_fleet_configuration(content: Path) -> None:
    (content / "hub.toml").write_text('[agents]\nmode = "hardlink"\n', encoding="utf-8")
    report = status(content)
    assert report.exit_code == 2
    assert [check.kind for check in report.checks] == ["config"]


def test_configuration_errors_become_one_error_check_with_exit_two(content: Path) -> None:
    (content / "hub.toml").write_text(
        '[agents]\nmode = "hardlink"\n', encoding="utf-8"
    )
    report = status(content)
    assert report.exit_code == 2
    assert report.problems == 1
    assert [check.kind for check in report.checks] == ["config"]
    assert report.checks[0].level == "ERROR"


def test_configuration_error_text_stays_on_one_line(
    content: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_repo: Path) -> operations.config.MachineProjection:
        raise operations.config.ConfigError("first line\nsecond line")

    monkeypatch.setattr(operations.config, "load_machine_projection", fail)
    report = status(content)
    assert report.checks[0].text == "first line second line"
