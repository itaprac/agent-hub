"""CLI acceptance: status keeps its human-readable prefixes and exit behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agenthub import config, core

from conftest import ROOT


def run(args: list[str], home: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT), **environment)
    env.pop("AGENT_HUB_MACHINE", None)
    return subprocess.run(
        args,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def module(home: Path, *extra: str, **environment: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "agenthub.cli", *extra], home, **environment)


def test_status_reports_missing_targets_and_exits_one(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "status")
    assert result.returncode == 1
    assert f"[MISSING] claude global/alpha: {home}/.claude/skills/alpha" in result.stdout


def test_status_on_an_applied_repository_exits_zero(content: Path, home: Path) -> None:
    core.apply_projection(config.load_machine_projection(content))
    result = module(home, "--repo", str(content), "status")
    assert result.returncode == 0
    assert f"[ok] claude global/alpha: {home}/.claude/skills/alpha" in result.stdout


def test_status_output_matches_the_structured_report(content: Path, home: Path) -> None:
    result = module(home, "--repo", str(content), "status")
    expected = [f"[{check.level}] {check.text}" for check in core.status(content).checks]
    assert result.stdout.splitlines() == expected


def test_status_uses_the_environment_override(content: Path, home: Path) -> None:
    result = module(home, "status", AGENT_HUB_REPO=str(content))
    assert result.returncode == 1
    assert "[MISSING] claude global/alpha" in result.stdout


def test_status_uses_the_repository_pointer(content: Path, home: Path) -> None:
    pointer = home / ".config" / "agent-hub" / "root"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{content}\n", encoding="utf-8")
    result = module(home, "status")
    assert result.returncode == 1
    assert "[MISSING] claude global/alpha" in result.stdout


def test_unreadable_configuration_exits_two(content: Path, home: Path) -> None:
    (content / "config" / "hub.toml").write_text("not = [toml\n", encoding="utf-8")
    result = module(home, "--repo", str(content), "status")
    assert result.returncode == 2
    assert "[ERROR]" in result.stderr


def test_compatibility_entry_point_matches_the_console_command(content: Path, home: Path) -> None:
    shim = run([sys.executable, str(ROOT / "hub.py"), "--repo", str(content), "status"], home)
    console = module(home, "--repo", str(content), "status")
    assert shim.returncode == console.returncode
    assert shim.stdout == console.stdout
