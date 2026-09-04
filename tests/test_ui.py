"""Console and scheduler CLI routes do not open a browser or install jobs in tests."""

from __future__ import annotations

import json
from pathlib import Path
import webbrowser

import pytest

from agenthub import cli, core, webapp


class ForegroundServer:
    def __init__(self, address, handler):
        self.requested_address = address
        self.server_address = (address[0], address[1] or 43210)
        self.closed = False
        self.handler = handler

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


def test_foreground_ui_prints_its_url_without_opening_a_browser(content, monkeypatch, capsys):
    instances = []

    def server(address, handler):
        instance = ForegroundServer(address, handler)
        instances.append(instance)
        return instance

    def forbidden_browser(*args, **kwargs):
        raise AssertionError("foreground Console must not open a browser")

    monkeypatch.setattr(webapp, "Server", server)
    monkeypatch.setattr(webbrowser, "open", forbidden_browser)
    monkeypatch.setattr(webbrowser, "open_new", forbidden_browser)
    monkeypatch.setattr(webbrowser, "open_new_tab", forbidden_browser)

    result = cli.main(["--store", str(content), "ui", "--port", "0"])

    assert result == 0
    assert "http://127.0.0.1:43210" in capsys.readouterr().out
    assert len(instances) == 1
    assert instances[0].requested_address == ("127.0.0.1", 0)
    assert instances[0].handler.repo == content
    assert instances[0].closed


def test_foreground_ui_reports_a_bind_failure(content, monkeypatch, capsys):
    def cannot_bind(*args):
        raise OSError("address already in use")

    monkeypatch.setattr(webapp, "Server", cannot_bind)
    assert cli.main(["ui", "--store", str(content)]) == 1
    assert "address already in use" in capsys.readouterr().err


def stub_report(content: Path, text: str) -> core.ApplyReport:
    return core.ApplyReport(
        machine_id="testmachine", hostname="testhost", repo=str(content),
        checks=(core.StatusCheck(kind="service", level="ok", text=text),), exit_code=0,
    )


@pytest.mark.parametrize("kind", ["timer", "ui"])
@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_service_commands_dispatch_without_starting_a_foreground_server(content, monkeypatch, capsys, kind, action):
    from agenthub import services

    calls = []

    def backend(selected_action, store):
        calls.append((selected_action, store))
        return stub_report(content, f"{kind} {action}")

    def forbidden_server(*args):
        raise AssertionError("service commands must not start a foreground server")

    monkeypatch.setattr(services, "timer" if kind == "timer" else "ui_service", backend)
    monkeypatch.setattr(webapp, "Server", forbidden_server)
    args = [kind, action] if kind == "timer" else ["ui", "--service", action]
    result = cli.main(["--store", str(content), *args])

    assert result == 0
    assert calls == [(action, content)]
    assert f"[ok] {kind} {action}" in capsys.readouterr().out


def test_timer_supports_global_json_after_the_subcommand(content, monkeypatch, capsys):
    from agenthub import services

    report = stub_report(content, "timer enabled")
    monkeypatch.setattr(services, "timer", lambda action, store: report)

    assert cli.main(["timer", "status", "--store", str(content), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == report.to_dict()


@pytest.mark.parametrize("arguments", [["timer", "on"], ["ui", "--service", "on"]])
def test_dry_run_does_not_call_service_backends(content, monkeypatch, arguments):
    from agenthub import services

    def forbidden_service(*args, **kwargs):
        raise AssertionError("unsupported dry-run must not install a service")

    monkeypatch.setattr(services, "timer", forbidden_service)
    monkeypatch.setattr(services, "ui_service", forbidden_service)
    with pytest.raises(SystemExit) as error:
        cli.main(["--store", str(content), "--dry-run", *arguments])
    assert error.value.code == 2


@pytest.mark.parametrize("kind", ["timer", "ui"])
@pytest.mark.parametrize("action", ["off", "status"])
def test_service_can_be_removed_or_inspected_when_store_is_missing(tmp_path, home, monkeypatch, kind, action):
    from agenthub import services

    missing = tmp_path / "missing-store"
    calls = []

    def backend(selected_action, store):
        calls.append((selected_action, store))
        return stub_report(missing, f"{kind} {action}")

    monkeypatch.setattr(services, "timer" if kind == "timer" else "ui_service", backend)
    args = [kind, action] if kind == "timer" else ["ui", "--service", action]
    assert cli.main(["--store", str(missing), *args]) == 0
    assert calls == [(action, missing)]
    assert not missing.exists()
