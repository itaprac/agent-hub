"""Make the local Web UI available during Setup."""

from __future__ import annotations

import os
import plistlib
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from . import fileio

SERVICE_LABEL = "com.agenthub.web"
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 7337
FILE_ACCESS_DENIAL_MARKER = "PermissionError: [Errno 1] Operation not permitted"


class _Runtime(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...

    def probe(self, url: str) -> bool: ...

    def sleep(self, seconds: float) -> None: ...

    def uid(self) -> int: ...

    def free_port(self) -> int: ...

    def start(self, command: list[str]) -> _Process: ...


class _Process(Protocol):
    stderr: IO[str] | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float) -> int: ...

    def kill(self) -> None: ...


class _SystemRuntime:
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )

    def probe(self, url: str) -> bool:
        command = os.environ.get("AGENT_HUB_SETUP_HTTP_PROBE")
        if command:
            result = self.run([command, url])
            return result.returncode == 0 and result.stdout == "200"
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def uid(self) -> int:
        return os.getuid()

    def free_port(self) -> int:
        configured_port = os.environ.get("AGENT_HUB_SETUP_SMOKE_PORT")
        if configured_port:
            return int(configured_port)
        with socket.socket() as listener:
            listener.bind((SERVICE_HOST, 0))
            return int(listener.getsockname()[1])

    def start(self, command: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )


@dataclass(frozen=True)
class LifecycleResult:
    foreground_command: str | None = None


@dataclass(frozen=True)
class RemovalResult:
    removed: bool


class WebLifecycleError(RuntimeError):
    """An operator-facing Web lifecycle failure."""


class WebLifecycle:
    """Own Web verification and macOS App service management."""

    def __init__(self, runtime: _Runtime | None = None) -> None:
        self._runtime = runtime or _SystemRuntime()

    def activate(
        self,
        *,
        system: str,
        home: Path,
        app_root: Path,
        web: Path,
        content: Path,
    ) -> LifecycleResult:
        if system != "Darwin":
            return self._verify_temporary(web, content, system)
        plist_path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        log_dir = home / "Library" / "Logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        service = {
            "Label": SERVICE_LABEL,
            "ProgramArguments": [
                str(web),
                "--host",
                SERVICE_HOST,
                "--port",
                str(SERVICE_PORT),
                "--quiet",
            ],
            "WorkingDirectory": str(app_root),
            "RunAtLoad": True,
            "KeepAlive": True,
            "EnvironmentVariables": {
                "HOME": str(home),
                "AGENT_HUB_REPO": "",
            },
            "StandardOutPath": str(log_dir / "agent-hub-web.log"),
            "StandardErrorPath": str(log_dir / "agent-hub-web.error.log"),
        }
        fileio.atomic_write(plist_path, plistlib.dumps(service))
        domain = f"gui/{self._runtime.uid()}"
        self._runtime.run(["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"])
        error_log = log_dir / "agent-hub-web.error.log"
        log_offset = error_log.stat().st_size if error_log.exists() else 0
        loaded = self._runtime.run(["launchctl", "bootstrap", domain, str(plist_path)])
        if loaded.returncode != 0:
            raise WebLifecycleError(
                loaded.stderr.strip() or f"could not load {SERVICE_LABEL}"
            )
        url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/"
        for _ in range(50):
            if self._runtime.probe(url):
                return LifecycleResult()
            self._runtime.sleep(0.1)
        raise self._service_start_error(url, error_log, log_offset, web, domain)

    def remove(self, *, system: str, home: Path) -> RemovalResult:
        if system != "Darwin":
            return RemovalResult(removed=False)
        plist_path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        domain = f"gui/{self._runtime.uid()}"
        loaded = self._runtime.run(["launchctl", "print", f"{domain}/{SERVICE_LABEL}"])
        if loaded.returncode == 0:
            removed = self._runtime.run(
                ["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"]
            )
            if removed.returncode != 0:
                raise WebLifecycleError(
                    removed.stderr.strip() or f"could not unload {SERVICE_LABEL}"
                )
        plist_path.unlink(missing_ok=True)
        return RemovalResult(removed=True)

    @staticmethod
    def _service_start_error(
        url: str, error_log: Path, log_offset: int, web: Path, domain: str
    ) -> WebLifecycleError:
        fresh_log = _read_log_after_offset(error_log, log_offset)
        message = f"reloaded service did not return HTTP 200 at {url}"
        if FILE_ACCESS_DENIAL_MARKER not in fresh_log:
            return WebLifecycleError(message)
        interpreter = (web.parent / "python").resolve()
        return WebLifecycleError(
            f"{message}\n"
            f"The service log shows a macOS file-access denial ({error_log}).\n"
            "macOS records the grant against the resolved interpreter path.\n"
            "A Homebrew Python upgrade changes that path and revokes the grant.\n"
            "Grant access again:\n"
            "  1. Open System Settings > Privacy & Security > Full Disk Access.\n"
            f"  2. Add this binary (press Cmd+Shift+G in the file picker): {interpreter}\n"
            f"  3. Run: launchctl kickstart -k {domain}/{SERVICE_LABEL}\n"
            "See docs/macos-permissions.md in the App repository."
        )

    def _verify_temporary(
        self, web: Path, content: Path, system: str
    ) -> LifecycleResult:
        port = self._runtime.free_port()
        process = self._runtime.start(
            [
                str(web),
                "--host",
                SERVICE_HOST,
                "--port",
                str(port),
                "--repo",
                str(content),
                "--quiet",
            ]
        )
        url = f"http://{SERVICE_HOST}:{port}/"
        try:
            for _ in range(50):
                if process.poll() is not None:
                    error = process.stderr.read().strip() if process.stderr else ""
                    raise WebLifecycleError(
                        error or "temporary Web process exited before verification"
                    )
                if self._runtime.probe(url):
                    foreground = None
                    if system == "Linux":
                        foreground = shlex.join(
                            [
                                str(web),
                                "--host",
                                SERVICE_HOST,
                                "--port",
                                str(SERVICE_PORT),
                            ]
                        )
                    return LifecycleResult(foreground_command=foreground)
                self._runtime.sleep(0.1)
            raise WebLifecycleError(
                f"temporary Web process did not return HTTP 200 at {url}"
            )
        finally:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _read_log_after_offset(path: Path, offset: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return ""
