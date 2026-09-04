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

HUB_TOML = """[agents]
enabled = ["claude"]
mode = "symlink"

[agents.claude]
name = "Claude"
universal = false
skills_global = "~/.claude/skills"
skills_project = ".claude/skills"
instructions_global = "~/.claude/CLAUDE.md"
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
    for variable in (
        "AGENT_HUB_STORE",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "AUTOHAND_HOME",
        "GROK_HOME",
        "HERMES_HOME",
        "VIBE_HOME",
    ):
        monkeypatch.delenv(variable, raising=False)
    write(directory / ".config" / "agent-hub" / "machine", f"{MACHINE_ID}\n")
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
    write(repo / "hub.toml", HUB_TOML)
    # Retained only for the setup and peer commands until their v2 migration.
    write(
        repo / "config" / "hub.toml",
        f'[machines]\n"{platform.node()}" = "{MACHINE_ID}"\nunused-host = "other-machine"\n',
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
