"""CLI acceptance: sync renders the shared structured report."""

from __future__ import annotations

from pathlib import Path

from agenthub import operations

from test_cli_status import module


def test_dry_run_output_matches_the_structured_report(content: Path, home: Path) -> None:
    expected = [
        f"[{check.level}] {check.text}" if check.level else check.text
        for check in operations.ContentOperations(content).sync(dry_run=True).checks
    ]
    result = module(home, "--repo", str(content), "--dry-run", "sync")
    assert result.returncode == 0
    assert result.stdout.splitlines() == expected


def test_sync_deploys_and_reports_a_clean_repository(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "sync")
    assert result.returncode == 0
    assert "[ok] git: nothing to commit" in result.stdout
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_unreadable_configuration_exits_two(content: Path, home: Path) -> None:
    (content / "config" / "hub.toml").write_text("not = [toml\n", encoding="utf-8")
    result = module(home, "--repo", str(content), "sync")
    assert result.returncode == 2
    assert "[ERROR]" in result.stderr
