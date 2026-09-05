"""Build and run the wheel offline, without a source checkout on sys.path."""

from __future__ import annotations

import configparser
import email
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile

import pytest

from agenthub import __version__
from conftest import ROOT


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("package")
    source = directory / "source"
    source.mkdir()
    for name in ("agenthub", "web"):
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD-PARTY.md", "hub.py", "web.py", "usage.py"):
        shutil.copy2(ROOT / name, source / name)
    output = directory / "dist"
    built = subprocess.run(
        [sys.executable, "-I", "-c", "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])", str(output)],
        cwd=source,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    [artifact] = output.glob("*.whl")
    return artifact


def test_release_metadata_and_runtime_version_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == __version__
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert project["license"] == "MIT"
    assert project["urls"]["Issues"].endswith("/agent-hub/issues")


def test_wheel_contains_metadata_entry_points_and_all_console_assets(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_path))
        assert metadata["Name"] == "agent-hub"
        assert metadata["Version"] == __version__
        assert metadata["License-Expression"] == "MIT"
        assert all('extra == "dev"' in requirement for requirement in metadata.get_all("Requires-Dist", []))
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert any(name.endswith(".dist-info/licenses/THIRD-PARTY.md") for name in names)
        for source in (ROOT / "web").rglob("*"):
            if source.is_file() and source.suffix in {".html", ".css", ".js"}:
                target = "agenthub/web_assets/" + source.relative_to(ROOT / "web").as_posix()
                assert archive.read(target) == source.read_bytes(), target
        assert json.loads(archive.read("agenthub/agents.json"))
        assert not any(".data/data/" in name or name.startswith("share/") for name in names)
        entry_points = configparser.ConfigParser()
        entry_points.read_string(archive.read(next(name for name in names if name.endswith(".dist-info/entry_points.txt"))).decode())
        assert entry_points["console_scripts"]["agent-hub"] == "agenthub.cli:main"
        assert entry_points["console_scripts"]["agent-hub-web"] == "agenthub.webapp:main"


def test_wheel_serves_packaged_assets_outside_the_source_tree(wheel: Path, tmp_path: Path, content: Path, home: Path) -> None:
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)
    unrelated = tmp_path / "unrelated"
    (unrelated / "web").mkdir(parents=True)
    (unrelated / "web" / "index.html").write_text("wrong assets\n")
    probe = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0,sys.argv[1]); from agenthub.webapp import WEB_ROOT; print(WEB_ROOT)", str(installed)],
        cwd=unrelated,
        env=dict(os.environ, HOME=str(home)),
        text=True,
        capture_output=True,
        check=True,
    )
    assert Path(probe.stdout.strip()) == installed / "agenthub" / "web_assets"
    log = tmp_path / "console.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-I", "-u", "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); from agenthub.webapp import main; raise SystemExit(main())", str(installed), "--store", str(content), "--host", "127.0.0.1", "--port", "0", "--quiet"],
            cwd=unrelated,
            env=dict(os.environ, HOME=str(home)),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            base = ""
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for line in log.read_text().splitlines():
                    if "http://127.0.0.1:" in line:
                        base = line[line.index("http://127.0.0.1:"):].split()[0]
                        break
                if base:
                    break
                assert process.poll() is None, log.read_text()
                time.sleep(0.02)
            assert base, log.read_text()
            for route, asset in (("/", "index.html"), ("/style.css", "style.css"), ("/js/app.js", "js/app.js")):
                with urllib.request.urlopen(base + route, timeout=5) as response:
                    assert response.status == 200
                    assert response.read() == (installed / "agenthub" / "web_assets" / asset).read_bytes()
            with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
                assert response.status == 200
                assert json.loads(response.read())["machine_id"] == "testmachine"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
