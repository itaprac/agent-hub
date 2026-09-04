"""Compatibility entry points start the installed package behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import usage
from agenthub import usage as package_usage
from agenthub import webapp

ROOT = Path(__file__).resolve().parents[1]


def test_web_entry_points_expose_the_same_command(tmp_path: Path) -> None:
    content = tmp_path / "content"
    root = subprocess.run(
        [sys.executable, str(ROOT / "web.py"), "--store", str(content)],
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    installed = subprocess.run(
        [str(Path(sys.executable).with_name("agent-hub-web")), "--store", str(content)],
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert root.returncode == installed.returncode == 2
    assert root.stderr == installed.stderr
    assert "Store directory not found" in root.stderr
    assert (webapp.WEB_ROOT / "index.html").is_file()


def test_usage_compatibility_module_aliases_the_package() -> None:
    assert usage is package_usage
