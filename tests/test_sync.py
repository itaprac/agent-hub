"""Structured sync: commit, pull, apply, and push through the shared package."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import config, core
from agenthub import gitio

from conftest import MACHINE_ID, git, write


def sync(repo: Path, dry_run: bool = False) -> core.SyncReport:
    return core.sync_report(config.load_machine_projection(repo), dry_run=dry_run)


def levels(report: core.SyncReport) -> list[str]:
    return [check.level for check in report.checks]


def texts(report: core.SyncReport) -> list[str]:
    return [check.text for check in report.checks]


def add_remote(content: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    git(content, "init", "-q", "--bare", str(bare))
    git(content, "remote", "add", "origin", str(bare))
    git(content, "push", "-q", "-u", "origin", "main")
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    return bare


def second_clone(bare: Path, tmp_path: Path) -> Path:
    checkout = tmp_path / "second-clone"
    git(tmp_path, "clone", "-q", str(bare), str(checkout))
    git(checkout, "config", "user.name", "agent-hub second clone")
    git(checkout, "config", "user.email", "second@example.invalid")
    return checkout


def test_clean_repo_without_remote_applies_without_network(
    content: Path, home: Path
) -> None:
    report = sync(content)
    assert report.exit_code == 0
    assert report.command == "sync"
    assert "git: nothing to commit" in texts(report)
    assert "git: no remote configured; pull and push disabled" in texts(report)
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_dirty_repo_without_remote_commits_and_applies(
    content: Path, home: Path
) -> None:
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    assert git(content, "log", "-1", "--format=%s").stdout.strip() == (
        f"hub sync: {MACHINE_ID}"
    )
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert not {"pull", "push"}.intersection(levels(report))


def test_remote_sync_pulls_applies_and_pushes(
    content: Path, home: Path, tmp_path: Path
) -> None:
    bare = add_remote(content, tmp_path)
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    assert "git pull --rebase" in texts(report)
    assert "git push" in texts(report)
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == git(
        content, "rev-parse", "HEAD"
    ).stdout


def test_sync_rebases_upstream_commits_and_pushes_local_work(
    content: Path, home: Path, tmp_path: Path
) -> None:
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(checkout / "upstream.md", "upstream work\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "upstream work")
    git(checkout, "push", "-q")
    write(content / "local.md", "local work\n")

    report = sync(content)
    assert report.exit_code == 0
    assert (content / "upstream.md").read_text(encoding="utf-8") == "upstream work\n"
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == git(
        content, "rev-parse", "HEAD"
    ).stdout


def test_sync_apply_uses_the_machine_projection_pulled_in_the_same_run(
    content: Path, home: Path, tmp_path: Path
) -> None:
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(
        checkout / "config" / "skills.toml",
        '[alpha]\nmachines = ["other-machine"]\n',
    )
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "restrict alpha to another machine")
    git(checkout, "push", "-q")

    report = sync(content)

    assert report.exit_code == 0
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_failed_rebase_stops_before_apply_and_push(
    content: Path, home: Path, tmp_path: Path
) -> None:
    write(content / "conflict.txt", "base\n")
    git(content, "add", "-A")
    git(content, "commit", "-q", "-m", "conflict base")
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(checkout / "conflict.txt", "upstream\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "upstream conflict")
    git(checkout, "push", "-q")
    upstream_head = git(bare, "rev-parse", "main").stdout
    write(content / "conflict.txt", "local\n")

    try:
        report = sync(content)
        assert report.exit_code == 1
        assert any(
            check.level == "ERROR"
            and "sync stopped after pull/rebase failure" in check.text
            for check in report.checks
        )
        assert not (home / ".claude" / "skills" / "alpha").is_symlink()
        assert git(bare, "rev-parse", "main").stdout == upstream_head
    finally:
        if (content / ".git" / "rebase-merge").exists():
            git(content, "rebase", "--abort")


def test_dry_run_reports_git_steps_without_changes(
    content: Path, home: Path, tmp_path: Path
) -> None:
    bare = add_remote(content, tmp_path)
    write(content / "local.md", "local work\n")
    status = git(content, "status", "--porcelain").stdout
    remote_head = git(bare, "rev-parse", "main").stdout

    report = sync(content, dry_run=True)
    assert report.exit_code == 0
    assert f"would run git add -A and commit: hub sync: {MACHINE_ID}" in texts(report)
    assert "would run git pull --rebase from origin/main" in texts(report)
    assert "would run git push" in texts(report)
    assert git(content, "status", "--porcelain").stdout == status
    assert not (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == remote_head


def test_remote_without_upstream_skips_pull_and_reports_the_failed_push(
    content: Path, home: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "remote.git"
    git(content, "init", "-q", "--bare", str(bare))
    git(content, "remote", "add", "origin", str(bare))

    report = sync(content)
    assert "git: remote exists but no upstream is configured; pull disabled" in texts(report)
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert report.exit_code == 1
    assert any(
        check.level == "ERROR" and "git push" in check.text for check in report.checks
    )


def test_sync_runs_git_only_in_the_content_repository(
    content: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    original = gitio.run_git

    def record(repo: Path, *args: str):
        calls.append(repo)
        return original(repo, *args)

    monkeypatch.setattr(gitio, "run_git", record)
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert calls
    assert set(calls) == {content}
