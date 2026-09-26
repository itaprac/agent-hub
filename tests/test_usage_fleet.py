"""Cross-machine totals, account API deduplication, and partial reads."""

import copy

import pytest

from agenthub import remote, usage, usage_fleet


def snapshot(cost=2.0, provider="codex"):
    bucket = {
        "day": "2026-09-05", "hourStart": None, "provider": provider, "model": "test",
        "totals": {**usage._empty_totals(), "outputTokens": 10},
        "costUsd": cost, "cacheSavingsUsd": 0, "records": 1, "sessions": 1, "unpricedRecords": 0,
    }
    sources = [{"provider": provider, "sessions": 1, "status": "ok", "scannedFiles": 1}]
    return {
        "days": 30, "timeZone": "Europe/Warsaw", "sinceDay": "2026-08-07", "untilDay": "2026-09-05",
        "resolution": "day", "readAt": "2026-09-05T09:00:00Z", "buckets": [bucket], "sources": sources,
        "rollups": usage._build_rollups([bucket], sources, "day"),
    }


@pytest.fixture
def setup(monkeypatch, home):
    usage_fleet._CACHE.clear()
    local = snapshot()
    monkeypatch.setattr(usage, "load_settings", lambda: {"claude": True, "codex": True, "grok": True, "cursor": True})
    monkeypatch.setattr(usage, "read_summary", lambda *_args: copy.deepcopy(local))
    target = remote.RemoteTarget("test@host", "/bin/agent-hub", "/store")
    monkeypatch.setattr(remote, "load_remotes", lambda: {"laptop": target})
    return local


def test_combines_machines_and_fetches_cursor_only_on_controller(setup, monkeypatch):
    other = snapshot(3)
    cursor = snapshot(100, "cursor")
    other["buckets"] += cursor["buckets"]
    other["sources"] += cursor["sources"]
    calls = []
    monkeypatch.setattr(remote, "read_usage", lambda *args: calls.append(args) or other)
    result = usage_fleet.read_summary()
    assert result["rollups"]["total"]["costUsd"] == 5
    assert result["rollups"]["total"]["sessions"] == 2
    assert result["rollups"]["daily"][0]["totalTokens"] == 20
    assert [row["machine"] for row in result["rollups"]["byMachine"]] == ["testmachine", "laptop"]
    assert result["partial"] is False
    result["buckets"].clear()
    assert len(usage_fleet.read_summary()["buckets"]) == 2
    assert len(calls) == 1


def test_failed_machine_keeps_local_totals_and_explicit_warning(setup, monkeypatch):
    def fail(*_args):
        raise remote.RemoteError("offline")
    monkeypatch.setattr(remote, "read_usage", fail)
    result = usage_fleet.read_summary()
    assert result["rollups"]["total"]["costUsd"] == 2
    assert result["partial"] is True
    assert result["machines"][1]["message"] == "offline"


@pytest.mark.parametrize("change", [
    {"timeZone": "UTC"}, {"sinceDay": "2026-08-01"}, {"buckets": [None]},
    {"sources": [{}]}, {"buckets": [{**snapshot()["buckets"][0], "costUsd": float("nan")}]},
])
def test_invalid_remote_report_is_not_added(setup, monkeypatch, change):
    monkeypatch.setattr(remote, "read_usage", lambda *_args: {**snapshot(), **change})
    result = usage_fleet.read_summary()
    assert result["partial"] is True
    assert result["rollups"]["total"]["costUsd"] == 2
