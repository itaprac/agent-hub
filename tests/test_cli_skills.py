"""CLI acceptance: add-skill and adopt keep their prefixes and exit behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agenthub import operations

from conftest import ROOT


def module(home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT))
    env.pop("AGENT_HUB_MACHINE", None)
    env.pop("AGENT_HUB_REPO", None)
    return subprocess.run(
        [sys.executable, "-m", "agenthub.cli", *extra],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_add_skill_creates_the_template_and_exits_zero(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "add-skill", "gamma")
    assert result.returncode == 0
    skill_file = content / "skills" / "global" / "gamma" / "SKILL.md"
    assert skill_file.is_file()
    assert f"[ok] created {skill_file}" in result.stdout


def test_add_skill_duplicate_exits_one(content: Path, home: Path) -> None:
    expected = operations.ContentOperations(content).add_skill("alpha")
    result = module(home, "--repo", str(content), "add-skill", "alpha")
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        f"[{check.level}] {check.text}" for check in expected.checks
    ]


def test_add_skill_unknown_project_exits_one(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "add-skill", "gamma", "--project", "nope")
    assert result.returncode == 1
    assert "[ERROR]" in result.stdout


def test_adopt_moves_the_skill_and_exits_zero(content: Path, home: Path) -> None:
    source = home / "local-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("# local-skill\n", encoding="utf-8")
    result = module(home, "--repo", str(content), "adopt", str(source))
    assert result.returncode == 0
    destination = content / "skills" / "global" / "local-skill"
    assert (destination / "SKILL.md").is_file()
    assert source.is_symlink()
    assert "[ok] adopted" in result.stdout
    assert "hub apply" in result.stdout


def test_adopt_collision_exits_one(content: Path, home: Path) -> None:
    source = home / "alpha"
    source.mkdir()
    (source / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    expected = operations.ContentOperations(content).adopt(str(source))
    result = module(home, "--repo", str(content), "adopt", str(source))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        f"[{check.level}] {check.text}" for check in expected.checks
    ]
    assert source.is_dir() and not source.is_symlink()
