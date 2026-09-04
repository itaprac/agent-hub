"""Remote actions preserve the target and serialize local Store refreshes."""

import urllib.error

import pytest

from agenthub import core, operations, remote
from test_web_apply import post


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(remote, "configured_machines", lambda: {"macbook"})
    checked = object()
    monkeypatch.setattr(remote, "check", lambda machine: checked)
    return checked


def report(machine="macbook", exit_code=0):
    return {"command": "sync", "machine_id": machine, "exit_code": exit_code,
            "lines": [{"level": "ok", "text": "finished"}]}


def test_remote_sync_publishes_then_runs_then_refreshes(server, configured, monkeypatch):
    calls = []

    def local(projection):
        calls.append("local")
        return core.SyncReport(projection.machine_id, projection.hostname, str(projection.repo), (), 0)

    def run(machine, command, dry_run=False, checked_target=None):
        assert checked_target is configured
        calls.append((machine, command, dry_run))
        with pytest.raises(operations.RepositoryBusyError):
            with operations._serialized():
                pass
        return report()

    monkeypatch.setattr(core, "sync_report", local)
    monkeypatch.setattr(remote, "run", run)
    result = post(server, "/api/run", {"command": "sync", "machine": "macbook"})
    assert calls == ["local", ("macbook", "sync", False), "local"]
    assert result["target_machine"] == "macbook"
    assert result["remote_exit_code"] == result["refresh_exit_code"] == 0


@pytest.mark.parametrize("command,dry_run", [("apply", False), ("sync", True)])
def test_apply_and_dry_run_do_not_sync_local_store(server, configured, monkeypatch, command, dry_run):
    monkeypatch.setattr(core, "sync_report", lambda *a, **kw: pytest.fail("local Sync must not run"))
    calls = []
    monkeypatch.setattr(remote, "run", lambda *a, **kw: calls.append((a, kw)) or report())
    post(server, "/api/run", {"command": command, "machine": "macbook", "dry_run": dry_run})
    assert calls == [(("macbook", command), {"dry_run": dry_run, "checked_target": configured})]


def test_failed_local_publish_does_not_start_remote(server, configured, monkeypatch):
    monkeypatch.setattr(core, "sync_report", lambda p: core.SyncReport(
        p.machine_id, p.hostname, str(p.repo), (), 1))
    monkeypatch.setattr(remote, "run", lambda *a, **kw: pytest.fail("must not start remote"))
    result = post(server, "/api/run", {"command": "sync", "machine": "macbook"})
    assert result["remote_started"] is False
    assert result["exit_code"] == 1


def test_failed_remote_preflight_does_not_change_local_store(server, configured, monkeypatch):
    def denied(machine):
        raise remote.RemoteError("Remote Machine ID does not match the configured target")

    monkeypatch.setattr(remote, "check", denied)
    monkeypatch.setattr(core, "sync_report", lambda *a, **kw: pytest.fail("must not sync local Store"))
    monkeypatch.setattr(remote, "run", lambda *a, **kw: pytest.fail("must not run remote command"))
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", {"command": "sync", "machine": "macbook"})
    assert error.value.code == 502


def test_failed_refresh_reports_remote_success_separately(server, configured, monkeypatch):
    codes = iter([0, 1])
    monkeypatch.setattr(core, "sync_report", lambda p: core.SyncReport(
        p.machine_id, p.hostname, str(p.repo), (), next(codes)))
    monkeypatch.setattr(remote, "run", lambda *a, **kw: report())
    result = post(server, "/api/run", {"command": "sync", "machine": "macbook"})
    assert result["remote_exit_code"] == 0
    assert result["exit_code"] == result["refresh_exit_code"] == 1
    assert "refreshing testmachine failed" in result["lines"][-1]["text"]


@pytest.mark.parametrize("body", [
    {"command": "sync", "machine": ""},
    {"command": "sync", "machine": ["macbook"]},
    {"command": "install", "machine": "macbook", "source": "x"},
    {"command": "sync", "machine": "macbook", "prefer": "remote"},
])
def test_remote_payload_rejected_before_operations(server, body, monkeypatch):
    monkeypatch.setattr(remote, "run", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", body)
    assert error.value.code == 400


def test_remote_action_requires_same_origin(server, configured, monkeypatch):
    monkeypatch.setattr(remote, "run", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", {"command": "sync", "machine": "macbook"}, headers={})
    assert error.value.code == 401
