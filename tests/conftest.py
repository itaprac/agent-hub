"""Shared fixtures: an isolated home, a fixture content repository, and a web server."""

from __future__ import annotations

import platform
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MACHINE_ID = "testmachine"

AGENTS_TOML = """[claude]
skills_global = "~/.claude/skills/{name}"
skills_project = "{project_root}/.claude/skills/{name}"
instructions_global = "~/.claude/CLAUDE.md"
instructions_project = "{project_root}/CLAUDE.md"
mode = "symlink"
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated HOME so deployed targets never touch the real machine."""
    directory = tmp_path / "home"
    (directory / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.delenv("AGENT_HUB_REPO", raising=False)
    monkeypatch.delenv("AGENT_HUB_MACHINE", raising=False)
    return directory


@pytest.fixture
def project(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def content(tmp_path: Path, home: Path, project: Path) -> Path:
    """A fixture Content repository with one global and one project skill."""
    repo = tmp_path / "content"
    hostname = platform.node()
    write(
        repo / "config" / "hub.toml",
        f'[machines]\n"{hostname}" = "{MACHINE_ID}"\nunused-host = "other-machine"\n',
    )
    write(repo / "config" / "agents.toml", AGENTS_TOML)
    write(
        repo / "config" / "projects.toml",
        f'[demo]\n{MACHINE_ID} = "{project}"\n\n[absent]\nother-machine = "~/absent"\n',
    )
    write(repo / "skills" / "global" / "alpha" / "SKILL.md", "# alpha\n")
    write(repo / "skills" / "projects" / "demo" / "beta" / "SKILL.md", "# beta\n")
    write(repo / "instructions" / "global" / "base.md", "Global base\n")
    write(repo / "instructions" / "projects" / "demo" / "base.md", "Project base\n")

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "agent-hub tests")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture content")
    return repo


@pytest.fixture
def server(content: Path, home: Path) -> Iterator[str]:
    from agenthub import webapp

    # A Content repo never ships hub.py, so a passing test proves that the web
    # application no longer runs the CLI inside the Content repo.
    assert not (content / "hub.py").exists()
    webapp.Handler.repo = content
    webapp.Handler.quiet = True
    instance = webapp.Server(("127.0.0.1", 0), webapp.Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)
