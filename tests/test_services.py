"""Service files and lifecycle commands run only against fake user managers."""

import os
from pathlib import Path
import plistlib
import subprocess

import pytest

from agenthub import services
from conftest import write


class Manager:
    def __init__(self, home):
        self.home = home
        self.calls = []
        self.loaded = set()
        self.enabled = set()
        self.fail = None
        self.denial = False

    def run(self, command):
        self.calls.append(command)
        if self.fail and self.fail in command:
            return subprocess.CompletedProcess(command, 1, "", "manager command denied")
        if command[0] == "launchctl":
            action = command[1]
            if action == "print":
                if command[2] not in self.loaded:
                    return subprocess.CompletedProcess(
                        command, 113, "", "Could not find service"
                    )
                text = "state = waiting\nruns = 2\nlast exit code = 0\nlast spawn time = 2026-09-05T01:00:00\n"
                if command[2].endswith("com.agenthub.web"):
                    text += "pid = 12345\n"
                return subprocess.CompletedProcess(command, 0, text, "")
            if action == "bootstrap":
                document = plistlib.loads(Path(command[3]).read_bytes())
                self.loaded.add(f"{command[2]}/{document['Label']}")
                if self.denial:
                    with Path(document["StandardErrorPath"]).open("a") as handle:
                        handle.write(services.FILE_ACCESS_DENIAL_MARKER + "\n")
            if action == "bootout":
                self.loaded.discard(command[2])
        elif command[0] == "systemctl":
            action = command[2]
            if action == "show":
                unit = command[3]
                exists = (self.home / ".config/systemd/user" / unit).exists()
                fields = {
                    "LoadState": "loaded" if exists else "not-found",
                    "ActiveState": "active" if unit in self.loaded else "inactive",
                    "UnitFileState": "enabled" if unit in self.enabled else "disabled",
                    "LastTriggerUSec": "2026-09-05 01:00:00 UTC",
                    "ExecMainStatus": "0",
                }
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "\n".join(f"{key}={value}" for key, value in fields.items()),
                    "",
                )
            if action == "enable":
                self.enabled.add(command[-1])
                self.loaded.add(command[-1])
            elif action == "disable":
                self.enabled.discard(command[-1])
                self.loaded.discard(command[-1])
        else:
            raise AssertionError(f"unexpected process: {command}")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def manager(home, tmp_path, monkeypatch):
    binary = tmp_path / 'bin space "quote" $cash %percent'
    binary.mkdir()
    for name in ("agent-hub", "agent-hub-web", "npx"):
        path = binary / name
        path.write_text("#!/usr/bin/python3\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary))
    monkeypatch.setattr(services.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(services.os, "getuid", lambda: 501)
    monkeypatch.setattr(services, "probe_ui", lambda store: True)
    monkeypatch.setattr(services.time, "sleep", lambda _: None)
    fake = Manager(home)
    monkeypatch.setattr(services, "run_command", fake.run)
    return fake


def assert_ok(report):
    assert report.exit_code == 0, report.lines()


def test_launchd_timer_plist_uses_resolved_commands_and_store_environment(
    content, home, manager
):
    report = services.timer("on", content)
    assert_ok(report)
    assert report.command == "timer on"
    plist = home / "Library/LaunchAgents/com.agenthub.sync.plist"
    document = plistlib.loads(plist.read_bytes())
    binary = Path(os.environ["PATH"]).resolve()
    assert document["ProgramArguments"] == [
        str(binary / "agent-hub"),
        "sync",
        "--quiet",
    ]
    assert document["StartInterval"] == 600
    assert document["RunAtLoad"] is False
    assert "KeepAlive" not in document
    assert document["WorkingDirectory"] == str(content)
    assert document["EnvironmentVariables"]["HOME"] == str(home)
    assert document["EnvironmentVariables"]["AGENT_HUB_STORE"] == str(content)
    assert str(binary) in document["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert document["StandardErrorPath"] == str(
        home / "Library/Logs/agent-hub-sync.error.log"
    )
    assert manager.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(plist)]


def test_launchd_on_off_and_status_are_idempotent(content, home, manager):
    assert_ok(services.timer("on", content))
    plist = home / "Library/LaunchAgents/com.agenthub.sync.plist"
    modified = plist.stat().st_mtime_ns
    before = len(manager.calls)
    assert_ok(services.timer("on", content))
    assert manager.calls[before:] == [
        ["launchctl", "print", "gui/501/com.agenthub.sync"]
    ]
    assert plist.stat().st_mtime_ns == modified
    status = services.timer("status", content)
    assert_ok(status)
    assert "last exit code = 0" in str(status.lines())
    assert "last spawn time" in str(status.lines())
    assert_ok(services.timer("off", content))
    assert not plist.exists()
    before = len(manager.calls)
    assert_ok(services.timer("off", content))
    assert len(manager.calls) == before + 1


def test_launchd_reloads_when_store_changes(content, home, tmp_path, manager):
    assert_ok(services.timer("on", content))
    changed = tmp_path / "alternate Store"
    changed.mkdir()
    before = len(manager.calls)
    assert_ok(services.timer("on", changed))
    assert [call[1] for call in manager.calls[before:]] == [
        "print",
        "bootout",
        "bootstrap",
    ]
    document = plistlib.loads(
        (home / "Library/LaunchAgents/com.agenthub.sync.plist").read_bytes()
    )
    assert document["EnvironmentVariables"]["AGENT_HUB_STORE"] == str(changed)


def test_launchd_ui_uses_fixed_local_bind_and_separate_logs(content, home, manager):
    report = services.ui_service("on", content)
    assert_ok(report)
    assert report.command == "ui --service on"
    document = plistlib.loads(
        (home / "Library/LaunchAgents/com.agenthub.web.plist").read_bytes()
    )
    assert document["ProgramArguments"][1:] == [
        "--host",
        "127.0.0.1",
        "--port",
        "7337",
        "--quiet",
    ]
    assert document["RunAtLoad"] and document["KeepAlive"]
    assert document["StandardOutPath"].endswith("agent-hub-web.log")
    assert_ok(services.ui_service("off", content))
    assert not (home / "Library/LaunchAgents/com.agenthub.web.plist").exists()


def test_launchctl_failure_is_reported_and_failed_off_keeps_plist(
    content, home, manager
):
    manager.fail = "bootstrap"
    report = services.timer("on", content)
    assert report.exit_code == 1 and "manager command denied" in str(report.lines())
    manager.fail = None
    assert_ok(services.timer("on", content))
    manager.fail = "bootout"
    report = services.timer("off", content)
    assert report.exit_code == 1
    assert (home / "Library/LaunchAgents/com.agenthub.sync.plist").exists()


def test_launchctl_inspection_permission_error_is_not_treated_as_off(content, manager):
    manager.fail = "print"
    report = services.timer("off", content)
    assert report.exit_code == 1
    assert "manager command denied" in str(report.lines())


@pytest.mark.parametrize("fresh_denial", [False, True])
def test_ui_start_failure_uses_only_fresh_tcc_errors(
    content, home, manager, monkeypatch, fresh_denial
):
    write(
        home / "Library/Logs/agent-hub-web.error.log",
        services.FILE_ACCESS_DENIAL_MARKER + "\n",
    )
    manager.denial = fresh_denial
    monkeypatch.setattr(services, "probe_ui", lambda store: False)
    report = services.ui_service("on", content)
    assert report.exit_code == 1
    text = str(report.lines())
    assert "did not return HTTP 200" in text
    assert "tail -n 40" in text
    assert ("Full Disk Access" in text) is fresh_denial
    assert ("docs/macos-permissions.md" in text) is fresh_denial


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_service_paths_do_not_follow_symlinks(
    content, home, tmp_path, manager, monkeypatch, system
):
    monkeypatch.setattr(services.platform, "system", lambda: system)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirect = home / "Library" if system == "Darwin" else home / ".config/systemd"
    redirect.symlink_to(outside, target_is_directory=True)
    report = services.timer("on", content)
    assert report.exit_code == 1
    assert "symlink" in str(report.lines())
    assert not list(outside.iterdir())
    assert not manager.calls


def test_resolved_npx_directory_is_kept_in_service_path(
    content, home, tmp_path, manager
):
    original_bin = Path(os.environ["PATH"])
    npx = original_bin / "npx"
    npx.unlink()
    resolved = tmp_path / "node-package/bin/npx-cli.js"
    write(resolved, "#!/usr/bin/env node\n")
    resolved.chmod(0o755)
    npx.symlink_to(resolved)
    assert_ok(services.timer("on", content))
    document = plistlib.loads(
        (home / "Library/LaunchAgents/com.agenthub.sync.plist").read_bytes()
    )
    paths = document["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert str(resolved.parent) in paths and str(original_bin) in paths


def test_systemd_timer_files_and_commands(content, home, manager, monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    assert_ok(services.timer("on", content))
    directory = home / ".config/systemd/user"
    service = (directory / "agent-hub-sync.service").read_text()
    timer = (directory / "agent-hub-sync.timer").read_text()
    assert "Type=oneshot" in service
    assert '"sync" "--quiet"' in service
    assert "OnActiveSec=600\nOnUnitActiveSec=600" in timer
    assert "Unit=agent-hub-sync.service" in timer
    assert 'Environment="AGENT_HUB_STORE=' + str(content) + '"' in service
    assert ["systemctl", "--user", "daemon-reload"] in manager.calls
    assert [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "agent-hub-sync.timer",
    ] in manager.calls
    status = services.timer("status", content)
    assert_ok(status)
    assert "LastTriggerUSec=2026-09-05" in str(status.lines())
    assert "last sync:" in str(status.lines())


def test_systemd_quotes_paths_without_shell_or_environment_expansion(
    content, home, manager, monkeypatch
):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    assert_ok(services.ui_service("on", content))
    text = (home / ".config/systemd/user/agent-hub-web.service").read_text()
    command = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert '\\"quote\\" $cash %%percent/agent-hub-web"' in command
    assert command.startswith("ExecStart=:")
    assert '"--host" "127.0.0.1" "--port" "7337" "--quiet"' in command
    environment = next(
        line for line in text.splitlines() if line.startswith('Environment="PATH=')
    )
    assert "$cash %%percent" in environment
    assert "$$cash" not in environment
    assert all(call[0] == "systemctl" for call in manager.calls)


def test_systemd_on_and_off_are_idempotent(content, home, manager, monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    assert_ok(services.timer("on", content))
    before = len(manager.calls)
    assert_ok(services.timer("on", content))
    assert [call[2] for call in manager.calls[before:]] == ["show"]
    assert_ok(services.timer("off", content))
    assert not (home / ".config/systemd/user/agent-hub-sync.timer").exists()
    assert not (home / ".config/systemd/user/agent-hub-sync.service").exists()
    before = len(manager.calls)
    assert_ok(services.timer("off", content))
    assert [call[2] for call in manager.calls[before:]] == ["show", "show"]


def test_systemd_failed_enable_is_an_error(content, manager, monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    manager.fail = "enable"
    report = services.timer("on", content)
    assert report.exit_code == 1
    assert "manager command denied" in str(report.lines())


def test_off_does_not_require_entrypoints_or_store_to_exist(
    home, tmp_path, manager, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert_ok(services.timer("off", tmp_path / "missing-store"))
    assert_ok(services.ui_service("off", tmp_path / "missing-store"))


def test_missing_entrypoint_and_unsupported_platform_are_errors(
    content, home, manager, monkeypatch
):
    monkeypatch.setenv("PATH", str(home / "missing"))
    report = services.timer("on", content)
    assert report.exit_code == 1 and "not on PATH" in str(report.lines())
    monkeypatch.setattr(services.platform, "system", lambda: "Windows")
    report = services.timer("on", content)
    assert report.exit_code == 1 and "not supported" in str(report.lines())


def test_launchd_status_reports_last_recorded_sync_when_no_run_time(content, manager):
    write(
        content / "machines/testmachine.json",
        '{"synced_at":"2026-09-05T01:20:00+00:00"}',
    )
    report = services.timer("status", content)
    assert_ok(report)
    assert "last recorded sync: 2026-09-05T01:20:00+00:00" in str(report.lines())


def test_systemd_off_stops_an_inflight_sync_before_removing_units(
    content, home, manager, monkeypatch
):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    assert_ok(services.timer("on", content))
    manager.loaded.add("agent-hub-sync.service")
    assert_ok(services.timer("off", content))
    assert ["systemctl", "--user", "stop", "agent-hub-sync.service"] in manager.calls
    assert not (home / ".config/systemd/user/agent-hub-sync.service").exists()


def test_failed_stop_keeps_systemd_service_files_for_recovery(
    content, home, manager, monkeypatch
):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    assert_ok(services.timer("on", content))
    manager.loaded.add("agent-hub-sync.service")
    manager.fail = "stop"
    report = services.timer("off", content)
    assert report.exit_code == 1
    assert (home / ".config/systemd/user/agent-hub-sync.service").exists()


@pytest.mark.parametrize(
    "payload,expected",
    [(b'{"repo":"/unrelated/store"}', False), (b"{}", False), (b"not JSON", False)],
)
def test_ui_probe_rejects_other_http_servers(content, monkeypatch, payload, expected):
    import io

    response = io.BytesIO(payload)
    response.status = 200
    monkeypatch.setattr(
        services.urllib.request, "urlopen", lambda *args, **kwargs: response
    )
    assert services.probe_ui(content) is expected


def test_ui_probe_accepts_the_selected_store(content, monkeypatch):
    import io
    import json

    response = io.BytesIO(json.dumps({"repo": str(content)}).encode())
    response.status = 200
    monkeypatch.setattr(
        services.urllib.request, "urlopen", lambda *args, **kwargs: response
    )
    assert services.probe_ui(content) is True
