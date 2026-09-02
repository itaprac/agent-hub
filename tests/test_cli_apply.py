"""CLI acceptance: apply keeps its human-readable prefixes and exit behavior."""

from __future__ import annotations

from pathlib import Path

from agenthub import operations

from test_cli_status import module


def test_apply_deploys_and_reports_each_action(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "apply")
    assert result.returncode == 0
    assert f"[link] claude global/alpha: {home}/.claude/skills/alpha" in result.stdout
    assert f"[render] claude global: {home}/.claude/CLAUDE.md" in result.stdout
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_dry_run_apply_changes_nothing(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "--dry-run", "apply")
    assert result.returncode == 0
    assert "[link]" in result.stdout
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_apply_output_matches_the_structured_report(content: Path, home: Path) -> None:
    expected = [
        f"[{check.level}] {check.text}"
        for check in operations.ContentOperations(content).apply(dry_run=True).checks
    ]
    result = module(home, "--repo", str(content), "--dry-run", "apply")
    assert result.stdout.splitlines() == expected


def test_drift_exits_one(content: Path, home: Path) -> None:
    target = home / ".claude" / "skills" / "alpha"
    target.mkdir(parents=True)
    (target / "keep.md").write_text("mine\n", encoding="utf-8")
    result = module(home, "--repo", str(content), "apply")
    assert result.returncode == 1
    assert "[DRIFT]" in result.stdout
    assert (target / "keep.md").exists()


def test_unreadable_configuration_exits_two(content: Path, home: Path) -> None:
    (content / "config" / "hub.toml").write_text("not = [toml\n", encoding="utf-8")
    result = module(home, "--repo", str(content), "apply")
    assert result.returncode == 2
    assert "[ERROR]" in result.stderr
