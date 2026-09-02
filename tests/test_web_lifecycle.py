"""Focused tests for the Setup Web lifecycle seam."""

from __future__ import annotations

import io
import plistlib
import subprocess
from pathlib import Path
from typing import IO, TypedDict

import pytest

from agenthub.web_lifecycle import WebLifecycle, WebLifecycleError


class Runtime:
    def __init__(self, probes: list[bool]) -> None:
        self.commands: list[list[str]] = []
        self.probes = iter(probes)
        self.sleeps: list[float] = []
        self.started: list[list[str]] = []
        self.process = Process()
        self.failures: dict[str, str] = {}
        self.bootstrap_log: tuple[Path, str] | None = None

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if len(command) > 1 and command[1] == "bootstrap" and self.bootstrap_log:
            path, text = self.bootstrap_log
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        error = self.failures.get(command[1] if len(command) > 1 else command[0])
        return subprocess.CompletedProcess(command, 2 if error else 0, "", error or "")

    def probe(self, url: str) -> bool:
        return next(self.probes)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def uid(self) -> int:
        return 501

    def free_port(self) -> int:
        return 17337

    def start(self, command: list[str]) -> Process:
        self.started.append(command)
        return self.process


class Process:
    def __init__(self) -> None:
        self.stderr: IO[str] | None = io.StringIO()
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class ActivationArguments(TypedDict):
    system: str
    home: Path
    app_root: Path
    web: Path
    content: Path


def test_activate_installs_and_starts_the_macos_service(tmp_path: Path) -> None:
    runtime = Runtime([True])
    home = tmp_path / "home"
    app = tmp_path / "app"
    web = app / ".venv" / "bin" / "agent-hub-web"

    result = WebLifecycle(runtime).activate(
        system="Darwin",
        home=home,
        app_root=app,
        web=web,
        content=tmp_path / "content",
    )

    plist_path = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    with plist_path.open("rb") as handle:
        service = plistlib.load(handle)
    assert service == {
        "Label": "com.agenthub.web",
        "ProgramArguments": [
            str(web),
            "--host",
            "127.0.0.1",
            "--port",
            "7337",
            "--quiet",
        ],
        "WorkingDirectory": str(app),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {"HOME": str(home), "AGENT_HUB_REPO": ""},
        "StandardOutPath": str(home / "Library" / "Logs" / "agent-hub-web.log"),
        "StandardErrorPath": str(home / "Library" / "Logs" / "agent-hub-web.error.log"),
    }
    assert runtime.commands == [
        ["launchctl", "bootout", "gui/501/com.agenthub.web"],
        ["launchctl", "bootstrap", "gui/501", str(plist_path)],
    ]
    assert result.foreground_command is None
    assert runtime.sleeps == []


def test_activate_verifies_a_temporary_web_process_on_linux(tmp_path: Path) -> None:
    runtime = Runtime([False, True])
    web = tmp_path / "app" / ".venv" / "bin" / "agent-hub-web"
    content = tmp_path / "content"

    result = WebLifecycle(runtime).activate(
        system="Linux",
        home=tmp_path / "home",
        app_root=tmp_path / "app",
        web=web,
        content=content,
    )

    assert runtime.started == [
        [
            str(web),
            "--host",
            "127.0.0.1",
            "--port",
            "17337",
            "--repo",
            str(content),
            "--quiet",
        ]
    ]
    assert runtime.sleeps == [0.1]
    assert runtime.process.terminated
    assert not runtime.process.killed
    assert result.foreground_command == (f"{web} --host 127.0.0.1 --port 7337")


def test_activate_reports_a_launchctl_bootstrap_failure(tmp_path: Path) -> None:
    runtime = Runtime([])
    runtime.failures["bootstrap"] = "forced launchctl bootstrap failure"

    with pytest.raises(WebLifecycleError, match="forced launchctl bootstrap failure"):
        WebLifecycle(runtime).activate(
            system="Darwin",
            home=tmp_path / "home",
            app_root=tmp_path / "app",
            web=tmp_path / "app" / ".venv" / "bin" / "agent-hub-web",
            content=tmp_path / "content",
        )


def test_activate_reports_a_macos_health_timeout(tmp_path: Path) -> None:
    runtime = Runtime([False] * 50)

    with pytest.raises(
        WebLifecycleError,
        match=("reloaded service did not return HTTP 200 at http://127.0.0.1:7337/"),
    ):
        WebLifecycle(runtime).activate(
            system="Darwin",
            home=tmp_path / "home",
            app_root=tmp_path / "app",
            web=tmp_path / "app" / ".venv" / "bin" / "agent-hub-web",
            content=tmp_path / "content",
        )

    assert runtime.sleeps == [0.1] * 50


def test_activate_diagnoses_a_current_run_macos_file_access_denial(
    tmp_path: Path,
) -> None:
    runtime = Runtime([False] * 50)
    home = tmp_path / "home"
    error_log = home / "Library" / "Logs" / "agent-hub-web.error.log"
    web = tmp_path / "app" / ".venv" / "bin" / "agent-hub-web"
    runtime.bootstrap_log = (
        error_log,
        "PermissionError: [Errno 1] Operation not permitted: '/Users/x/content'\n",
    )

    with pytest.raises(WebLifecycleError) as raised:
        WebLifecycle(runtime).activate(
            system="Darwin",
            home=home,
            app_root=tmp_path / "app",
            web=web,
            content=tmp_path / "content",
        )

    message = str(raised.value)
    assert "reloaded service did not return HTTP 200" in message
    assert "Full Disk Access" in message
    assert str((web.parent / "python").resolve()) in message
    assert "launchctl kickstart -k gui/501/com.agenthub.web" in message
    assert "Homebrew" in message


def test_remove_unloads_and_deletes_the_macos_service(tmp_path: Path) -> None:
    runtime = Runtime([])
    home = tmp_path / "home"
    plist_path = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("installed", encoding="utf-8")

    result = WebLifecycle(runtime).remove(system="Darwin", home=home)

    assert runtime.commands == [
        ["launchctl", "print", "gui/501/com.agenthub.web"],
        ["launchctl", "bootout", "gui/501/com.agenthub.web"],
    ]
    assert not plist_path.exists()
    assert result.removed


def test_activate_reloads_an_existing_macos_service(tmp_path: Path) -> None:
    runtime = Runtime([True, True])
    lifecycle = WebLifecycle(runtime)
    home = tmp_path / "home"
    arguments: ActivationArguments = {
        "system": "Darwin",
        "home": home,
        "app_root": tmp_path / "app",
        "web": tmp_path / "app" / ".venv" / "bin" / "agent-hub-web",
        "content": tmp_path / "content",
    }

    lifecycle.activate(**arguments)
    plist_path = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    first_plist = plist_path.read_bytes()
    lifecycle.activate(**arguments)

    assert plist_path.read_bytes() == first_plist
    assert [command[1] for command in runtime.commands] == [
        "bootout",
        "bootstrap",
        "bootout",
        "bootstrap",
    ]


def test_activate_ignores_macos_file_access_denials_from_before_reload(
    tmp_path: Path,
) -> None:
    runtime = Runtime([False] * 50)
    home = tmp_path / "home"
    error_log = home / "Library" / "Logs" / "agent-hub-web.error.log"
    error_log.parent.mkdir(parents=True)
    error_log.write_text(
        "PermissionError: [Errno 1] Operation not permitted: '/Users/x/content'\n",
        encoding="utf-8",
    )

    with pytest.raises(WebLifecycleError) as raised:
        WebLifecycle(runtime).activate(
            system="Darwin",
            home=home,
            app_root=tmp_path / "app",
            web=tmp_path / "app" / ".venv" / "bin" / "agent-hub-web",
            content=tmp_path / "content",
        )

    assert "did not return HTTP 200" in str(raised.value)
    assert "Full Disk Access" not in str(raised.value)


def test_activate_does_not_misdiagnose_an_unrelated_permission_error(
    tmp_path: Path,
) -> None:
    runtime = Runtime([False] * 50)
    home = tmp_path / "home"
    error_log = home / "Library" / "Logs" / "agent-hub-web.error.log"
    runtime.bootstrap_log = (
        error_log,
        "PermissionError: [Errno 13] Permission denied: '/tmp/locked'\n",
    )

    with pytest.raises(WebLifecycleError) as raised:
        WebLifecycle(runtime).activate(
            system="Darwin",
            home=home,
            app_root=tmp_path / "app",
            web=tmp_path / "app" / ".venv" / "bin" / "agent-hub-web",
            content=tmp_path / "content",
        )

    assert "did not return HTTP 200" in str(raised.value)
    assert "Full Disk Access" not in str(raised.value)
