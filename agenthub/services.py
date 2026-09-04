"""Install the optional Sync Timer and Console as user services."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from . import config, core, fileio

FILE_ACCESS_DENIAL_MARKER = "PermissionError: [Errno 1] Operation not permitted"


@dataclass(frozen=True)
class ServiceReport(core.Report):
    action: str = "status"
    service: str = "timer"

    @property
    def command(self) -> str:
        return (
            f"timer {self.action}"
            if self.service == "timer"
            else f"ui --service {self.action}"
        )


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Process seam for tests. Service commands never run through a shell."""
    return subprocess.run(
        command, text=True, capture_output=True, check=False, timeout=30
    )


def probe_ui(store: Path) -> bool:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:7337/api/state", timeout=0.25
        ) as response:
            if response.status != 200:
                return False
            state = json.load(response)
            return (
                isinstance(state, dict)
                and isinstance(state.get("repo"), str)
                and Path(state["repo"]).resolve() == store.resolve()
            )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _require(command: list[str]) -> str:
    result = run_command(command)
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"{command[0]} exited {result.returncode}"
        )
    return result.stdout.strip()


def _safe_path(home: Path, path: Path) -> None:
    if not path.is_relative_to(home):
        raise ValueError(f"{path}: service path is outside HOME")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{current}: service paths must not be symlinks")
        if current != path and current.exists() and not current.is_dir():
            raise ValueError(f"{current}: service parent must be a directory")
    if path.exists() and not path.is_file():
        raise ValueError(f"{path}: service file must be a regular file")


def _executable(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise ValueError(
            f"{name} is not on PATH; install agent-hub before enabling a service"
        )
    return Path(found).resolve()


def _environment(home: Path, store: Path, executable: Path) -> dict[str, str]:
    paths = [str(executable.parent)]
    npx = shutil.which("npx")
    if npx:
        paths.extend((str(Path(npx).parent), str(Path(npx).resolve().parent)))
    paths.extend(part for part in os.environ.get("PATH", "").split(os.pathsep) if part)
    for fallback in ("/usr/local/bin", "/usr/bin", "/bin"):
        paths.append(fallback)
    return {
        "HOME": str(home),
        "AGENT_HUB_STORE": str(store),
        "PATH": os.pathsep.join(dict.fromkeys(paths)),
    }


def _write(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fileio.atomic_write(path, content, 0o600)
    return True


def _launch_state(target: str) -> tuple[bool, str]:
    result = run_command(["launchctl", "print", target])
    if result.returncode == 0:
        return True, result.stdout
    message = (result.stderr + result.stdout).lower()
    if any(
        text in message
        for text in (
            "could not find service",
            "could not find specified service",
            "no such process",
            "service not found",
        )
    ):
        return False, ""
    raise RuntimeError(
        result.stderr.strip()
        or result.stdout.strip()
        or "could not inspect launchd service"
    )


def _fresh_log(path: Path, offset: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(offset if path.stat().st_size >= offset else 0)
            return handle.read(65536).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _interpreter(executable: Path) -> Path:
    try:
        with executable.open("rb") as handle:
            first = handle.readline(2048).decode("utf-8")
        if first.startswith("#!"):
            words = shlex.split(first[2:].strip())
            if words:
                if Path(words[0]).name == "env" and len(words) > 1:
                    found = shutil.which(words[1])
                    if found:
                        return Path(found).resolve()
                return Path(words[0]).resolve()
    except (OSError, UnicodeError, ValueError):
        pass
    return executable


def _ui_start_error(error_log: Path, offset: int, executable: Path, domain: str) -> str:
    message = f"Console service did not return HTTP 200; inspect {error_log}"
    fresh = _fresh_log(error_log, offset)
    if FILE_ACCESS_DENIAL_MARKER in fresh:
        message += (
            "; macOS denied file access. A Python upgrade changes the interpreter path. "
            "Open System Settings > Privacy & Security > Full Disk Access, press Cmd+Shift+G, "
            f"and add {_interpreter(executable)}. Then run launchctl kickstart -k {domain}/com.agenthub.web. "
            "See docs/macos-permissions.md."
        )
    message += f" Read recent errors with: tail -n 40 {shlex.quote(str(error_log))}"
    return message


def _wait_ui(store: Path) -> bool:
    for _ in range(20):
        if probe_ui(store):
            return True
        time.sleep(0.25)
    return False


def _last_recorded_sync(store: Path) -> str:
    machine, _ = config.resolve_machine()
    directory = store / "machines"
    path = directory / f"{machine}.json"
    if directory.is_symlink() or path.is_symlink():
        return "unknown"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        timestamp = record.get("synced_at") if isinstance(record, dict) else None
        return timestamp if isinstance(timestamp, str) else "unknown"
    except (OSError, ValueError):
        return "unknown"


def _launchd(
    action: str, store: Path, home: Path, service: str
) -> list[core.StatusCheck]:
    name = "sync" if service == "timer" else "web"
    label = f"com.agenthub.{name}"
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    plist = home / "Library/LaunchAgents" / f"{label}.plist"
    _safe_path(home, plist)
    loaded, state = _launch_state(target)
    checks: list[core.StatusCheck] = []

    def note(level: str, text: str) -> None:
        checks.append(
            core.StatusCheck(kind="service", level=level, text=text, target=str(plist))
        )

    if action == "status":
        fields = [
            line.strip()
            for line in state.splitlines()
            if any(
                key in line
                for key in (
                    "state =",
                    "last exit code =",
                    "last terminating signal =",
                    "runs =",
                    "pid =",
                    "last spawn time =",
                )
            )
        ]
        note(
            "ok" if loaded else "skip",
            f"{label}: {'loaded' if loaded else 'off'}"
            + ("; " + "; ".join(fields) if fields else ""),
        )
        if service == "timer":
            note("ok", f"last recorded sync: {_last_recorded_sync(store)}")
        note(
            "ok",
            f"logs: {home / 'Library/Logs' / ('agent-hub-' + name + '.log')} and {home / 'Library/Logs' / ('agent-hub-' + name + '.error.log')}",
        )
        return checks
    if action == "off":
        if loaded:
            _require(["launchctl", "bootout", target])
        plist.unlink(missing_ok=True)
        note("ok", f"{label}: off")
        return checks
    if not store.is_dir():
        raise ValueError(f"Store directory not found: {store}")
    executable = _executable("agent-hub" if service == "timer" else "agent-hub-web")
    arguments = (
        [str(executable), "sync", "--quiet"]
        if service == "timer"
        else [str(executable), "--host", "127.0.0.1", "--port", "7337", "--quiet"]
    )
    stdout = home / "Library/Logs" / f"agent-hub-{name}.log"
    stderr = home / "Library/Logs" / f"agent-hub-{name}.error.log"
    for path in (stdout, stderr):
        _safe_path(home, path)
    document = {
        "Label": label,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(store),
        "EnvironmentVariables": _environment(home, store, executable),
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
        "RunAtLoad": service == "ui",
    }
    if service == "timer":
        document["StartInterval"] = 600
    else:
        document["KeepAlive"] = True
    content = plistlib.dumps(document)
    changed = not plist.exists() or plist.read_bytes() != content
    offset = stderr.stat().st_size if stderr.exists() else 0
    if loaded and changed:
        _require(["launchctl", "bootout", target])
        loaded = False
    stdout.parent.mkdir(parents=True, exist_ok=True)
    _write(plist, content)
    if not loaded:
        try:
            _require(["launchctl", "bootstrap", domain, str(plist)])
        except RuntimeError as exc:
            if service == "ui":
                raise RuntimeError(
                    f"{exc}; {_ui_start_error(stderr, offset, executable, domain)}"
                ) from exc
            raise
    if service == "ui" and not _wait_ui(store):
        raise RuntimeError(_ui_start_error(stderr, offset, executable, domain))
    if service == "ui":
        active, current_state = _launch_state(target)
        if not active or not re.search(r"(?m)^\s*pid\s*=\s*\d+", current_state):
            raise RuntimeError(
                f"{label} is not running; {_ui_start_error(stderr, offset, executable, domain)}"
            )
    note(
        "ok",
        f"{label}: on"
        + (
            "; sync every 600 seconds"
            if service == "timer"
            else "; Console listens on 127.0.0.1:7337"
        ),
    )
    return checks


def _quote_unit(value: str) -> str:
    if "\x00" in value:
        raise ValueError("service values must not contain NUL")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("%", "%%")
    )
    return '"' + escaped + '"'


def _unit_files(store: Path, home: Path, service: str) -> dict[str, bytes]:
    name = "sync" if service == "timer" else "web"
    executable = _executable("agent-hub" if service == "timer" else "agent-hub-web")
    arguments = (
        [str(executable), "sync", "--quiet"]
        if service == "timer"
        else [str(executable), "--host", "127.0.0.1", "--port", "7337", "--quiet"]
    )
    lines = [
        "[Unit]",
        f"Description=agent-hub {name}",
        "",
        "[Service]",
        "Type=oneshot" if service == "timer" else "Type=exec",
        "ExecStart=:" + " ".join(_quote_unit(value) for value in arguments),
    ]
    lines.extend(
        "Environment=" + _quote_unit(f"{key}={value}")
        for key, value in _environment(home, store, executable).items()
    )
    if service == "ui":
        lines.extend(
            (
                "Restart=on-failure",
                "RestartSec=5",
                "",
                "[Install]",
                "WantedBy=default.target",
            )
        )
    units = {f"agent-hub-{name}.service": ("\n".join(lines) + "\n").encode()}
    if service == "timer":
        units["agent-hub-sync.timer"] = (
            "[Unit]\nDescription=Sync the agent-hub Store every 600 seconds\n\n"
            "[Timer]\nOnActiveSec=600\nOnUnitActiveSec=600\nAccuracySec=1\n"
            "Unit=agent-hub-sync.service\n\n[Install]\nWantedBy=timers.target\n"
        ).encode()
    return units


def _systemd_state(unit: str) -> dict[str, str]:
    result = run_command(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=LoadState,ActiveState,UnitFileState,Result,ExecMainStatus,ExecMainExitTimestamp,LastTriggerUSec,NextElapseUSecRealtime",
        ]
    )
    state = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    if result.returncode and state.get("LoadState") != "not-found":
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "could not inspect systemd unit"
        )
    if not state:
        raise RuntimeError("systemctl returned no unit state")
    return state


def _systemd(
    action: str, store: Path, home: Path, service: str
) -> list[core.StatusCheck]:
    directory = home / ".config/systemd/user"
    unit = "agent-hub-sync.timer" if service == "timer" else "agent-hub-web.service"
    names = ["agent-hub-sync.service", unit] if service == "timer" else [unit]
    for name in names:
        _safe_path(home, directory / name)
    state = _systemd_state(unit)
    checks: list[core.StatusCheck] = []

    def note(level: str, text: str) -> None:
        checks.append(
            core.StatusCheck(
                kind="service", level=level, text=text, target=str(directory / unit)
            )
        )

    if action == "status":
        note(
            "ok" if state.get("ActiveState") == "active" else "skip",
            f"{unit}: " + "; ".join(f"{key}={value}" for key, value in state.items()),
        )
        if service == "timer" and state.get("LoadState") != "not-found":
            last = _systemd_state("agent-hub-sync.service")
            note(
                "ok",
                "last sync: "
                + "; ".join(f"{key}={value}" for key, value in last.items()),
            )
        note(
            "ok",
            f"logs: journalctl --user -u agent-hub-{'sync' if service == 'timer' else 'web'}.service -n 40",
        )
        return checks
    if action == "off":
        if state.get("LoadState") != "not-found":
            _require(["systemctl", "--user", "disable", "--now", unit])
        if service == "timer":
            sync_state = _systemd_state("agent-hub-sync.service")
            if sync_state.get("ActiveState") in {"active", "activating", "reloading"}:
                _require(["systemctl", "--user", "stop", "agent-hub-sync.service"])
        changed = False
        for name in names:
            path = directory / name
            if path.exists():
                path.unlink()
                changed = True
        if changed:
            _require(["systemctl", "--user", "daemon-reload"])
        note("ok", f"{unit}: off")
        return checks
    if not store.is_dir():
        raise ValueError(f"Store directory not found: {store}")
    generated = _unit_files(store, home, service)
    changed = False
    for name, content in generated.items():
        changed = _write(directory / name, content) or changed
    if changed:
        _require(["systemctl", "--user", "daemon-reload"])
    active = state.get("ActiveState") == "active"
    enabled = state.get("UnitFileState") == "enabled"
    if changed or not active or not enabled:
        _require(["systemctl", "--user", "enable", "--now", unit])
        if changed and active:
            _require(["systemctl", "--user", "restart", unit])
    if service == "ui" and not _wait_ui(store):
        raise RuntimeError(
            "Console service did not return HTTP 200; inspect journalctl --user -u agent-hub-web.service -n 40"
        )
    if service == "ui" and _systemd_state(unit).get("ActiveState") != "active":
        raise RuntimeError(
            f"{unit} is not running; inspect journalctl --user -u {unit} -n 40"
        )
    note(
        "ok",
        f"{unit}: on"
        + (
            "; sync every 600 seconds"
            if service == "timer"
            else "; Console listens on 127.0.0.1:7337"
        ),
    )
    return checks


def _service(action: str, store: Path, service: str) -> ServiceReport:
    machine, hostname = "", ""
    checks: list[core.StatusCheck] = []
    try:
        machine, hostname = config.resolve_machine()
        if action not in {"on", "off", "status"}:
            raise ValueError("service action must be on, off, or status")
        home = Path.home().resolve()
        store = Path(store).expanduser().resolve()
        system = platform.system()
        if system == "Darwin":
            checks = _launchd(action, store, home, service)
        elif system == "Linux":
            checks = _systemd(action, store, home, service)
        else:
            raise ValueError(f"user services are not supported on {system}")
    except (
        config.ConfigError,
        ValueError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        checks.append(
            core.StatusCheck(
                kind="service", level="ERROR", text=core.one_line(str(exc))
            )
        )
    return ServiceReport(
        machine_id=machine,
        hostname=hostname,
        repo=str(store),
        checks=tuple(checks),
        exit_code=int(any(check.level == "ERROR" for check in checks)),
        action=action,
        service=service,
    )


def timer(action: str, store: Path) -> ServiceReport:
    return _service(action, store, "timer")


def ui_service(action: str, store: Path) -> ServiceReport:
    return _service(action, store, "ui")
