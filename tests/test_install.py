"""skills.sh integration uses a local npx stub and an isolated Store."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error

import pytest

from agenthub import operations, skills
from conftest import git, write
from test_cli_status import module
from test_web_apply import post

STUB = r"""
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
Path(os.environ['NPX_ARGUMENT_LOG']).write_text(json.dumps({'args': args, 'xdg_state_home': os.environ.get('XDG_STATE_HOME'), 'cwd': os.getcwd()}))
mode = os.environ.get('NPX_BEHAVIOR', '')
if mode == 'wait':
    Path(os.environ['NPX_READY']).write_text('ready')
    while not Path(os.environ['NPX_RELEASE']).exists():
        time.sleep(.01)
if mode == 'child-timeout':
    import subprocess
    child_code = "import os, signal, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(os.environ['NPX_CHILD_READY']).write_text(str(os.getpid())); time.sleep(.6); (Path.home() / '.agents' / 'late-write').write_text('escaped')"
    subprocess.Popen([sys.executable, '-c', child_code])
    time.sleep(10)
if mode == 'timeout':
    time.sleep(10)
if mode == 'fail':
    print('stub failure', file=sys.stderr)
    raise SystemExit(7)
if mode == 'noop':
    print('no changes')
    raise SystemExit(0)
root = Path.home() / '.agents'
lock_path = root / '.skill-lock.json'
lock = json.loads(lock_path.read_text()) if lock_path.exists() else {'version': 3, 'skills': {}}
if args[2] == 'add':
    selected = args[args.index('--skill') + 1] if '--skill' in args else 'installed-skill'
    names = [selected]
    source = args[3]
else:
    names = args[4:] or list(lock['skills'])
    source = 'fixture/source'
for name in names:
    directory = root / 'skills' / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text('# ' + name + '\n' + args[2] + '\n')
    lock['skills'][name] = {'source': source, 'sourceType': 'github', 'sourceUrl': 'https://example.invalid/source', 'installedAt': '2026-01-01T00:00:00Z', 'updatedAt': '2026-09-05T00:00:00Z'}
if mode == 'file-changes':
    (directory / 'obsolete.txt').unlink()
    (directory / 'run.sh').chmod(0o755)
    (directory / 'literal[1].txt').write_text('new')
lock_path.write_text(json.dumps(lock))
if mode == 'partial-fail':
    raise SystemExit(7)
print('stub completed')
"""


@pytest.fixture
def npx(content: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = home / "bin"
    executable = directory / "npx"
    write(executable, f"#!{sys.executable}\n" + STUB)
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory) + ":" + os.environ.get("PATH", ""))
    monkeypatch.setenv("NPX_ARGUMENT_LOG", str(home / "npx-arguments.json"))
    (home / ".agents").symlink_to(content, target_is_directory=True)
    return executable


def arguments(home: Path) -> dict:
    return json.loads((home / "npx-arguments.json").read_text())


def succeeded(report) -> None:
    assert report.exit_code == 0, report.lines()


def test_missing_npx_returns_install_hint_without_any_mutation(
    content: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skills.shutil, "which", lambda _: None)
    before = git(content, "rev-parse", "HEAD").stdout
    report = operations.ContentOperations(content).install("fixture/source")
    assert report.exit_code == 1
    assert report.checks[0].level == "ERROR"
    assert "Node.js" in report.checks[0].text and "npx" in report.checks[0].text
    assert git(content, "rev-parse", "HEAD").stdout == before
    assert git(content, "status", "--porcelain").stdout == ""
    assert not (home / ".agents").exists()
    assert not (home / ".claude" / "skills").exists()


def test_install_runs_literal_argv_applies_and_commits_only_installer_changes(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(content / "AGENTS.md", "Unrelated staged instructions\n")
    git(content, "add", "AGENTS.md")
    write(content / "skills" / "alpha" / "SKILL.md", "Unrelated unstaged Skill edit\n")
    write(content / "notes.txt", "Unrelated untracked notes\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "outside-state"))
    source = "fixture/source; $(touch should-not-exist)"

    report = operations.ContentOperations(content).install(source, "downloaded")

    succeeded(report)
    assert report.command == "install"
    assert arguments(home) == {
        "args": ["-y", "skills", "add", source, "-g", "-y", "--skill", "downloaded"],
        "xdg_state_home": None,
        "cwd": str(content),
    }
    assert (
        home / ".claude" / "skills" / "downloaded"
    ).resolve() == content / "skills" / "downloaded"
    assert not (content / "should-not-exist").exists()
    assert git(content, "show", "HEAD:AGENTS.md").stdout == "Global base\n"
    assert git(content, "show", "HEAD:skills/alpha/SKILL.md").stdout == "# alpha\n"
    assert git(content, "diff", "--cached", "--name-only").stdout == "AGENTS.md\n"
    assert git(content, "diff", "--name-only").stdout == "skills/alpha/SKILL.md\n"
    assert set(
        git(content, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    ) == {".skill-lock.json", "skills/downloaded/SKILL.md"}
    assert (content / "notes.txt").read_text() == "Unrelated untracked notes\n"


def test_update_accepts_names_and_does_not_create_empty_commits(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = operations.ContentOperations(content)
    succeeded(store.install("fixture/source", "downloaded"))
    succeeded(store.update(["downloaded"]))
    assert arguments(home)["args"] == ["-y", "skills", "update", "-g", "downloaded"]
    head = git(content, "rev-parse", "HEAD").stdout
    write(content / "AGENTS.md", "Do not commit this\n")
    git(content, "add", "AGENTS.md")
    monkeypatch.setenv("NPX_BEHAVIOR", "noop")
    succeeded(store.update())
    assert arguments(home)["args"] == ["-y", "skills", "update", "-g"]
    assert git(content, "rev-parse", "HEAD").stdout == head
    assert git(content, "diff", "--cached", "--name-only").stdout == "AGENTS.md\n"


def test_mismatched_canonical_store_rejects_before_npx(
    content: Path, home: Path, npx: Path
) -> None:
    (home / ".agents").unlink()
    (home / ".agents").mkdir()
    report = operations.ContentOperations(content).install("fixture/source")
    assert report.exit_code == 1
    assert "init --store" in report.checks[-1].text
    assert not (home / "npx-arguments.json").exists()
    assert git(content, "status", "--porcelain").stdout == ""
    assert list((home / ".agents").iterdir()) == []


@pytest.mark.parametrize("unsafe", ["skills", ".skill-lock.json"])
def test_installer_rejects_symlinked_store_paths(
    content: Path, home: Path, npx: Path, unsafe: str
) -> None:
    if unsafe == "skills":
        (content / "skills").rename(content / "saved-skills")
        (content / "skills").symlink_to(
            content / "saved-skills", target_is_directory=True
        )
    else:
        write(home / "outside-lock.json", '{"version":3,"skills":{}}')
        (content / unsafe).symlink_to(home / "outside-lock.json")
    before = git(content, "status", "--porcelain").stdout
    report = operations.ContentOperations(content).install("fixture/source")
    assert report.exit_code == 1
    assert "symlink" in report.checks[-1].text
    assert not (home / "npx-arguments.json").exists()
    assert git(content, "status", "--porcelain").stdout == before


@pytest.mark.parametrize("mode", ["fail", "partial-fail", "timeout"])
def test_subprocess_failure_skips_apply_and_commit(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("NPX_BEHAVIOR", mode)
    monkeypatch.setattr(skills, "INSTALL_TIMEOUT", 0.1)
    before = git(content, "rev-parse", "HEAD").stdout
    report = operations.ContentOperations(content).install("fixture/source")
    assert report.exit_code == 1
    assert "ERROR" == report.checks[-1].level
    assert "Apply and commit were not run" in report.checks[-1].text
    assert git(content, "rev-parse", "HEAD").stdout == before
    assert not (home / ".claude" / "skills").exists()


def test_corrupt_lockfile_is_preserved_and_reported_in_state(
    content: Path, home: Path, npx: Path
) -> None:
    write(content / ".skill-lock.json", "not json\n")
    report = operations.ContentOperations(content).install("fixture/source")
    assert report.exit_code == 1
    assert not (home / "npx-arguments.json").exists()
    assert (content / ".skill-lock.json").read_text() == "not json\n"
    state = operations.ContentOperations(content).state()
    assert state["warnings"] and ".skill-lock.json" in state["warnings"][0]
    assert state["skills"]["global"][0]["installed"] is False


def test_state_distinguishes_installed_and_handwritten_skills(
    content: Path, home: Path, npx: Path
) -> None:
    succeeded(
        operations.ContentOperations(content).install("fixture/source", "downloaded")
    )
    state = operations.ContentOperations(content).state()
    entries = {item["name"]: item for item in state["skills"]["global"]}
    assert entries["alpha"]["installed"] is False
    assert entries["alpha"]["provenance"] is None
    assert entries["downloaded"]["installed"] is True
    assert entries["downloaded"]["provenance"] == {
        "source": "fixture/source",
        "source_type": "github",
        "source_url": "https://example.invalid/source",
        "installed_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-09-05T00:00:00Z",
    }
    assert state["hub_config_exists"] is True and state["warnings"] == []
    assert state["store"] == str(content)
    agents = {item["name"]: item for item in state["agents"]}
    assert agents["claude"]["enabled"] is True
    assert agents["claude-code"]["detected"] is True
    assert agents["codex"]["universal"] is True
    assert agents["codex"]["enabled"] is False


def test_cli_install_and_named_update(content: Path, home: Path, npx: Path) -> None:
    installed = module(
        home,
        "--store",
        str(content),
        "install",
        "fixture/source",
        "--skill",
        "downloaded",
        "--json",
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert json.loads(installed.stdout)["command"] == "install"
    updated = module(home, "--store", str(content), "update", "downloaded", "--json")
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert json.loads(updated.stdout)["command"] == "update"
    assert arguments(home)["args"][-2:] == ["-g", "downloaded"]


def test_http_install_and_update(
    server: str, content: Path, home: Path, npx: Path
) -> None:
    installed = post(
        server,
        "/api/run",
        {"command": "install", "source": "fixture/source", "skill": "downloaded"},
    )
    assert installed["exit_code"] == 0, installed["lines"]
    updated = post(server, "/api/run", {"command": "update", "names": ["downloaded"]})
    assert updated["exit_code"] == 0, updated["lines"]
    assert arguments(home)["args"][-2:] == ["-g", "downloaded"]


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "install"},
        {"command": "install", "source": 3},
        {"command": "install", "source": "--all"},
        {"command": "install", "source": "fixture/source", "skill": "../escape"},
        {"command": "install", "source": "fixture/source", "dry_run": True},
        {"command": "update", "names": "downloaded"},
        {"command": "update", "names": [3]},
        {"command": "update", "names": ["--all"]},
    ],
)
def test_invalid_http_install_payload_does_not_run_npx(
    server: str, content: Path, home: Path, npx: Path, payload: dict
) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", payload)
    assert error.value.code == 400
    assert not (home / "npx-arguments.json").exists()
    assert git(content, "status", "--porcelain").stdout == ""


def test_install_holds_the_store_lock_during_subprocess(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NPX_BEHAVIOR", "wait")
    ready, release = home / "npx-ready", home / "npx-release"
    monkeypatch.setenv("NPX_READY", str(ready))
    monkeypatch.setenv("NPX_RELEASE", str(release))
    reports = []
    store = operations.ContentOperations(content)
    thread = threading.Thread(
        target=lambda: reports.append(store.install("fixture/source"))
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(operations.RepositoryBusyError, match="store is busy"):
            store.update()
        with pytest.raises(operations.RepositoryBusyError, match="store is busy"):
            store.state()
    finally:
        release.write_text("continue")
        thread.join(timeout=10)
    assert not thread.is_alive()
    succeeded(reports[0])


def test_update_commits_removed_files_and_executable_changes(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = operations.ContentOperations(content)
    succeeded(store.install("fixture/source", "downloaded"))
    directory = content / "skills" / "downloaded"
    write(directory / "obsolete.txt", "remove me\n")
    write(directory / "run.sh", "#!/bin/sh\necho fixture\n")
    git(content, "config", "core.filemode", "true")
    git(content, "add", "skills")
    git(content, "commit", "-m", "old upstream files")
    monkeypatch.setenv("NPX_BEHAVIOR", "file-changes")

    succeeded(store.update(["downloaded"]))

    assert git(content, "status", "--porcelain").stdout == ""
    assert (
        "100755" in git(content, "ls-tree", "HEAD", "skills/downloaded/run.sh").stdout
    )
    assert (
        git(content, "ls-tree", "HEAD", "skills/downloaded/obsolete.txt").stdout == ""
    )
    assert git(content, "show", "HEAD:skills/downloaded/literal[1].txt").stdout == "new"


def test_timeout_stops_child_writes_before_releasing_store_lock(
    content: Path, home: Path, npx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = home / "child-ready"
    monkeypatch.setenv("NPX_BEHAVIOR", "child-timeout")
    monkeypatch.setenv("NPX_CHILD_READY", str(marker))
    monkeypatch.setattr(skills, "INSTALL_TIMEOUT", 0.3)
    store = operations.ContentOperations(content)
    head = git(content, "rev-parse", "HEAD").stdout

    report = store.install("fixture/source")

    assert report.exit_code == 1 and "timed out" in report.checks[-1].text
    assert "partial downloads may remain" in report.checks[-1].text
    assert marker.is_file(), "the child must start before the timeout"
    store.state()  # The operation released its lock after process cleanup.
    time.sleep(0.7)
    assert not (content / "late-write").exists()
    assert git(content, "rev-parse", "HEAD").stdout == head
    assert git(content, "status", "--porcelain").stdout == ""


def test_commit_handles_directory_replaced_by_internal_symlink(content: Path) -> None:
    directory = content / "skills" / "alpha"
    write(directory / "docs" / "note.md", "old docs\n")
    write(directory / "new-docs" / "note.md", "new docs\n")
    git(content, "add", "skills")
    git(content, "commit", "-m", "old upstream tree")
    before = skills._snapshot(content)
    (directory / "docs" / "note.md").unlink()
    (directory / "docs").rmdir()
    (directory / "docs").symlink_to("new-docs", target_is_directory=True)

    assert skills._commit_changes(content, before, "update")

    assert git(content, "status", "--porcelain").stdout == ""
    assert "120000" in git(content, "ls-tree", "HEAD", "skills/alpha/docs").stdout
    assert git(content, "show", "HEAD:skills/alpha/docs").stdout == "new-docs"
