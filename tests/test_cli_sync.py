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
    result = module(home, "--store", str(content), "--dry-run", "sync")
    assert result.returncode == 0
    assert result.stdout.splitlines() == expected


def test_sync_deploys_and_reports_a_clean_repository(content: Path, home: Path) -> None:
    result = module(home, "--store", str(content), "sync")
    assert result.returncode == 0
    assert "[ok] git: nothing to commit" in result.stdout
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_unreadable_configuration_exits_two(content: Path, home: Path) -> None:
    (content / "hub.toml").write_text("not = [toml\n", encoding="utf-8")
    result = module(home, "--store", str(content), "sync")
    assert result.returncode == 2
    assert "[ERROR]" in result.stderr


def test_two_machines_clone_sync_and_report_content_lag(content: Path, home: Path, tmp_path: Path) -> None:
    import json

    from conftest import MACHINE_ID, git, write
    from test_sync import add_remote

    write(content / ".gitignore", ".DS_Store\n*.local.*\n")
    bare = add_remote(content, tmp_path)
    first = module(home, "--store", str(content), "sync")
    assert first.returncode == 0, first.stdout + first.stderr

    other_home = tmp_path / "other-home"
    write(other_home / ".config" / "agent-hub" / "machine", "second-machine\n")
    (other_home / ".claude").mkdir()
    author = {
        "GIT_AUTHOR_NAME": "second Machine",
        "GIT_AUTHOR_EMAIL": "second@example.invalid",
        "GIT_COMMITTER_NAME": "second Machine",
        "GIT_COMMITTER_EMAIL": "second@example.invalid",
    }
    cloned = module(other_home, "init", "--from", str(bare), "--yes", **author)
    assert cloned.returncode == 0, cloned.stdout + cloned.stderr
    other_store = other_home / ".agents"
    git(other_store, "config", "user.name", "second Machine")
    git(other_store, "config", "user.email", "second@example.invalid")
    assert module(other_home, "sync").returncode == 0
    assert module(home, "--store", str(content), "sync").returncode == 0

    write(content / "skills" / "alpha" / "SKILL.md", "# changed on first Machine\n")
    updated = module(home, "--store", str(content), "sync")
    assert updated.returncode == 0, updated.stdout + updated.stderr
    status = module(home, "--store", str(content), "status", "--fleet", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    rows = {item["machine"]: item for item in json.loads(status.stdout)["fleet"]}
    assert set(rows) == {MACHINE_ID, "second-machine"}
    assert rows[MACHINE_ID]["current"] is True
    assert rows[MACHINE_ID]["local"] is True
    assert rows["second-machine"]["current"] is False
    assert rows["second-machine"]["behind"] == 1
    assert rows["second-machine"]["local"] is False

    assert module(other_home, "sync").returncode == 0
    assert module(home, "--store", str(content), "sync").returncode == 0
    status = module(home, "--store", str(content), "status", "--fleet", "--json")
    assert status.returncode == 0
    assert all(item["current"] for item in json.loads(status.stdout)["fleet"])
    assert (other_home / ".claude" / "skills" / "alpha" / "SKILL.md").read_text() == "# changed on first Machine\n"


def test_cli_prefer_resolves_conflict(content: Path, home: Path, tmp_path: Path) -> None:
    from test_sync import conflict_remote

    conflict_remote(content, tmp_path)
    result = module(home, "--store", str(content), "sync", "--prefer", "remote")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (content / "conflict.txt").read_text() == "upstream\n"
    assert (content / "local-only.txt").read_text() == "keep local work\n"
