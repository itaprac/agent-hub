"""Sync integration with isolated Agent paths and local Git remotes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenthub import config, core, gitio

from conftest import MACHINE_ID, git, write


def sync(repo: Path, dry_run: bool = False, prefer: str | None = None) -> core.SyncReport:
    return core.sync_report(config.load_machine_projection(repo), dry_run=dry_run, prefer=prefer)


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


def record(content: Path) -> dict:
    return json.loads((content / "machines" / f"{MACHINE_ID}.json").read_text())


def conflict_remote(content: Path, tmp_path: Path) -> Path:
    write(content / "conflict.txt", "base\n")
    git(content, "add", "-A")
    git(content, "commit", "-q", "-m", "conflict base")
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(checkout / "conflict.txt", "upstream\n")
    write(checkout / "remote-only.txt", "keep upstream work\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "upstream conflict")
    git(checkout, "push", "-q")
    write(content / "conflict.txt", "local\n")
    write(content / "local-only.txt", "keep local work\n")
    return bare


def test_clean_repo_without_remote_applies_and_records_without_network(content: Path, home: Path) -> None:
    head = git(content, "rev-parse", "HEAD").stdout.strip()
    report = sync(content)
    assert report.exit_code == 0
    assert report.command == "sync"
    assert any(check.level == "skip" and "no remote" in check.text for check in report.checks)
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert record(content)["head"] == head
    assert record(content)["machine"] == MACHINE_ID
    assert record(content)["status"] == {"exit_code": 0, "problems": 0}
    assert git(content, "status", "--porcelain").stdout == ""


def test_dirty_repo_without_remote_commits_content_before_record(content: Path, home: Path) -> None:
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    head = record(content)["head"]
    assert git(content, "show", "-s", "--format=%s", head).stdout.strip() == f"sync: {MACHINE_ID}"
    assert git(content, "show", f"{head}:local.md").stdout == "local work\n"
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert not {"pull", "push"}.intersection(levels(report))


def test_remote_sync_pulls_applies_and_pushes(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == git(content, "rev-parse", "HEAD").stdout
    assert json.loads(git(bare, "show", f"main:machines/{MACHINE_ID}.json").stdout) == record(content)


def test_sync_rebases_upstream_commits_and_pushes_local_work(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(checkout / "upstream.md", "upstream work\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "upstream work")
    git(checkout, "push", "-q")
    write(content / "local.md", "local work\n")

    report = sync(content)

    assert report.exit_code == 0
    assert (content / "upstream.md").read_text() == "upstream work\n"
    assert (content / "local.md").read_text() == "local work\n"
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == git(content, "rev-parse", "HEAD").stdout


def test_sync_apply_uses_the_configuration_pulled_in_the_same_run(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    checkout = second_clone(bare, tmp_path)
    write(checkout / "hub.toml", (checkout / "hub.toml").read_text() + '\n[skills.alpha]\nmachines = ["other-machine"]\n')
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "restrict alpha to another machine")
    git(checkout, "push", "-q")

    report = sync(content)

    assert report.exit_code == 0
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_conflict_aborts_rebase_and_preserves_local_commit_without_apply(content: Path, home: Path, tmp_path: Path) -> None:
    bare = conflict_remote(content, tmp_path)
    upstream_head = git(bare, "rev-parse", "main").stdout

    report = sync(content)

    assert report.exit_code == 1
    assert any(check.level == "CONFLICT" and "conflict.txt" in check.text for check in report.checks)
    assert not (home / ".claude" / "skills" / "alpha").is_symlink()
    assert not (content / "machines" / f"{MACHINE_ID}.json").exists()
    assert not (content / ".git" / "rebase-merge").exists()
    assert not (content / ".git" / "rebase-apply").exists()
    assert (content / "conflict.txt").read_text() == "local\n"
    assert (content / "local-only.txt").read_text() == "keep local work\n"
    assert not (content / "remote-only.txt").exists()
    assert git(content, "status", "--porcelain").stdout == ""
    assert git(content, "log", "-1", "--format=%s").stdout.strip() == f"sync: {MACHINE_ID}"
    assert git(bare, "rev-parse", "main").stdout == upstream_head


@pytest.mark.parametrize(("prefer", "expected"), [("local", "local\n"), ("remote", "upstream\n")])
def test_preferred_side_resolves_conflict_and_keeps_other_work(content: Path, home: Path, tmp_path: Path, prefer: str, expected: str) -> None:
    bare = conflict_remote(content, tmp_path)

    report = sync(content, prefer=prefer)

    assert report.exit_code == 0, texts(report)
    assert "CONFLICT" not in levels(report)
    assert (content / "conflict.txt").read_text() == expected
    assert (content / "local-only.txt").read_text() == "keep local work\n"
    assert (content / "remote-only.txt").read_text() == "keep upstream work\n"
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(content, "status", "--porcelain").stdout == ""
    assert git(bare, "rev-parse", "main").stdout == git(content, "rev-parse", "HEAD").stdout


def test_unreachable_origin_warns_and_continues_apply(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    remote_head = git(bare, "rev-parse", "main").stdout
    git(content, "remote", "set-url", "origin", str(tmp_path / "missing-origin.git"))
    write(content / "skills" / "offline" / "SKILL.md", "offline work\n")

    report = sync(content)

    assert report.exit_code == 0, texts(report)
    assert any(check.level == "warn" and "origin unreachable" in check.text and "push pending" in check.text for check in report.checks)
    assert "ERROR" not in levels(report)
    assert (home / ".claude" / "skills" / "offline").is_symlink()
    assert record(content)["status"] == {"exit_code": 0, "problems": 0}
    assert git(bare, "rev-parse", "main").stdout == remote_head


def test_apply_drift_is_recorded_and_pushed(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    write(home / ".claude" / "skills" / "alpha" / "local.txt", "keep\n")

    report = sync(content)

    assert report.exit_code == 1
    assert record(content)["status"]["exit_code"] == 1
    assert record(content)["status"]["problems"] > 0
    assert (home / ".claude" / "skills" / "alpha" / "local.txt").read_text() == "keep\n"
    assert git(bare, "rev-parse", "main").stdout == git(content, "rev-parse", "HEAD").stdout


def test_dry_run_reports_steps_without_changes(content: Path, home: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    write(content / "local.md", "local work\n")
    status = git(content, "status", "--porcelain").stdout
    remote_head = git(bare, "rev-parse", "main").stdout
    head = git(content, "rev-parse", "HEAD").stdout

    report = sync(content, dry_run=True)

    assert report.exit_code == 0
    assert f"would run git add -A and commit: sync: {MACHINE_ID}" in texts(report)
    assert any("pull" in text for text in texts(report))
    assert any("push" in text for text in texts(report))
    assert git(content, "status", "--porcelain").stdout == status
    assert git(content, "rev-parse", "HEAD").stdout == head
    assert not (content / "machines").exists()
    assert not (home / ".claude" / "skills" / "alpha").is_symlink()
    assert git(bare, "rev-parse", "main").stdout == remote_head


def test_remote_without_upstream_skips_network_and_applies(content: Path, home: Path, tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    git(content, "init", "-q", "--bare", str(bare))
    git(content, "remote", "add", "origin", str(bare))

    report = sync(content)

    assert report.exit_code == 0, texts(report)
    assert any(check.level == "skip" and "upstream" in check.text for check in report.checks)
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_sync_runs_git_only_in_the_store(content: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    original = gitio.run_git

    def capture(repo: Path, *args: str, **kwargs):
        calls.append(repo)
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(gitio, "run_git", capture)
    write(content / "local.md", "local work\n")
    report = sync(content)
    assert report.exit_code == 0
    assert calls and set(calls) == {content}


@pytest.mark.parametrize("prefer", ["local", "remote"])
def test_preference_resolves_local_delete_against_remote_edit(content: Path, home: Path, tmp_path: Path, prefer: str) -> None:
    bare = conflict_remote(content, tmp_path)
    (content / "conflict.txt").unlink()

    report = sync(content, prefer=prefer)

    assert report.exit_code == 0, texts(report)
    if prefer == "local":
        assert not (content / "conflict.txt").exists()
    else:
        assert (content / "conflict.txt").read_text() == "upstream\n"
    assert (content / "local-only.txt").read_text() == "keep local work\n"
    assert (content / "remote-only.txt").read_text() == "keep upstream work\n"
    assert git(content, "status", "--porcelain").stdout == ""
    assert git(bare, "rev-parse", "main").stdout == git(content, "rev-parse", "HEAD").stdout


def test_remote_policy_rejection_is_an_error(content: Path, tmp_path: Path) -> None:
    bare = add_remote(content, tmp_path)
    hook = bare / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\necho 'Store policy rejects this push' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    write(content / "policy-test.md", "local content\n")

    report = sync(content)

    assert report.exit_code == 1
    assert any(check.level == "ERROR" and "push failed" in check.text
               and "Store policy rejects this push" in check.text for check in report.checks)
    assert not any(check.level == "warn" and "push rejected" in check.text for check in report.checks)
    assert (content / "policy-test.md").read_text() == "local content\n"
    assert git(content, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("message", [
    "fatal: Authentication failed",
    "Permission denied (publickey). Could not read from remote repository",
    "remote: Repository not found. Could not read from remote repository",
    "fatal: unable to access origin: The requested URL returned error: 403",
])
def test_remote_credentials_failure_stops_before_apply(content, home, tmp_path, monkeypatch, message):
    from agenthub import gitio
    import subprocess
    add_remote(content, tmp_path)
    original = gitio.run_git
    def failing_pull(repo, *args, **kwargs):
        if args[:2] == ("pull", "--rebase"):
            return subprocess.CompletedProcess(args, 128, "", message)
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(gitio, "run_git", failing_pull)
    report = sync(content)
    assert report.exit_code == 1
    assert report.problems > 0
    assert not (home / ".claude/skills/alpha").exists()
    assert not (content / "machines/testmachine.json").exists()
