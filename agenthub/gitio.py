"""Git plumbing shared by the fleet operations and the web application."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git failure with the detail that the caller reports to the operator."""


class GitCommandError(GitError):
    """A git command exited with an error."""


class GitOutputError(GitError):
    """A git command produced output that cannot be parsed."""


def run_git(
    repo: Path, *args: str, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        seconds = f" after {timeout}s" if timeout is not None else ""
        raise GitCommandError(f"git {' '.join(args)} timed out{seconds}") from None
    except OSError as exc:
        raise GitCommandError(f"cannot run git: {exc}") from exc


def is_repository(repo: Path) -> bool:
    result = run_git(repo, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def dirty(repo: Path) -> tuple[bool, list[str]]:
    result = run_git(repo, "status", "--porcelain")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    lines = [line for line in result.stdout.splitlines() if line]
    return bool(lines), lines


def remotes(repo: Path) -> list[str]:
    result = run_git(repo, "remote")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def upstream(repo: Path) -> str | None:
    result = run_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return result.stdout.strip() if result.returncode == 0 else None


def divergence(repo: Path) -> tuple[int, int]:
    """Return commits ahead of and behind the upstream branch."""
    result = run_git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if result.returncode != 0:
        raise GitCommandError(result.stderr.strip() or "git rev-list failed")
    try:
        behind_text, ahead_text = result.stdout.split()
        return int(ahead_text), int(behind_text)
    except (ValueError, TypeError):
        raise GitOutputError(result.stdout.strip()) from None


def _require(repo: Path, *args: str, timeout: int | None = None) -> str:
    result = run_git(repo, *args, timeout=timeout)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise GitCommandError(message)
    return result.stdout.strip()


def state(repo: Path, fetch: bool = True, fetch_timeout: int = 5) -> dict[str, object]:
    """Return the Content repository state used by the web console and Peers."""
    fetch_error: str | None = None
    upstream_result = run_git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    remote = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
    if fetch and remote is not None:
        try:
            fetched = run_git(repo, "fetch", "--quiet", timeout=fetch_timeout)
        except GitCommandError as exc:
            fetch_error = str(exc)
        else:
            if fetched.returncode != 0:
                fetch_error = (
                    fetched.stderr.strip() or fetched.stdout.strip() or "git fetch failed"
                )

    branch_result = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "HEAD"
    head_raw = _require(repo, "log", "-1", "--format=%H%x00%h%x00%s%x00%cI")
    head_parts = head_raw.split("\x00", 3)
    if len(head_parts) != 4:
        raise GitOutputError("unexpected git log output")

    status_result = run_git(repo, "status", "--porcelain")
    if status_result.returncode != 0:
        raise GitCommandError(status_result.stderr.strip() or "git status failed")

    ahead: int | None = None
    behind: int | None = None
    if remote is not None:
        counts = run_git(repo, "rev-list", "--left-right", "--count", f"HEAD...{remote}")
        if counts.returncode != 0:
            message = counts.stderr.strip() or counts.stdout.strip() or "cannot compare git revisions"
            raise GitCommandError(message)
        try:
            ahead, behind = (int(value) for value in counts.stdout.split())
        except (TypeError, ValueError):
            raise GitOutputError("unexpected git rev-list output") from None

    return {
        "branch": branch,
        "head": {
            "sha": head_parts[0],
            "short": head_parts[0][:7],
            "subject": head_parts[2],
            "date": head_parts[3],
        },
        "dirty": len(status_result.stdout.splitlines()),
        "ahead": ahead,
        "behind": behind,
        "remote": remote,
        "fetch_error": fetch_error,
    }
