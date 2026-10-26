"""Plan limits from Codex's app-server and Claude Code's status line."""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from agenthub import cli, config, operations, plan_limits, remote, usage, usage_fleet
from conftest import HUB_TOML, write

SAME_ORIGIN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}

FAKE_CODEX = """#!{python}
import json
import sys

assert sys.argv[1:] == ["app-server"], sys.argv
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({{"id": message["id"], "result": {{"userAgent": "fake"}}}}), flush=True)
    elif message.get("method") == "account/rateLimits/read":
        print(json.dumps({{"method": "account/updated", "params": {{}}}}), flush=True)
        print(json.dumps({{"id": message["id"], "result": {result}}}), flush=True)
"""

CODEX_RESULT = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 23, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
        "secondary": {"usedPercent": 41.5, "windowDurationMins": 10080, "resetsAt": 1_800_500_000},
        "credits": {"hasCredits": True, "unlimited": False, "balance": "12.5"},
        "planType": "prolite",
        "rateLimitReachedType": None,
    },
    "rateLimitsByLimitId": {
        "codex": {"limitId": "codex"},
        "codex_spark": {
            "limitId": "codex_spark", "limitName": "Spark",
            "primary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1_800_600_000},
        },
    },
    "rateLimitResetCredits": {
        "availableCount": 2,
        "credits": [
            {"status": "available", "title": "Full reset", "grantedAt": 1_700_000_000, "expiresAt": 1_900_000_000},
            {"status": "redeemed", "title": "Full reset", "expiresAt": 1_800_000_000},
            {"status": "available", "title": "Full reset", "grantedAt": 1_700_000_000, "expiresAt": 1_850_000_000},
        ],
    },
}


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    plan_limits.clear_cache()


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(
        FAKE_CODEX.format(python=sys.executable, result=repr(json.loads(json.dumps(CODEX_RESULT)))),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return script


def test_codex_limits_come_from_the_app_server(home: Path, fake_codex: Path) -> None:
    plans = plan_limits.read_limits({"claude": False, "codex": True})["plans"]

    assert plans == [{
        "provider": "codex", "status": "ok", "capturedAt": plans[0]["capturedAt"],
        "plan": "prolite",
        "windows": [
            {"id": "codex:primary", "limit": None, "windowMinutes": 300,
             "usedPercent": 23.0, "resetsAt": 1_800_000_000_000},
            {"id": "codex:secondary", "limit": None, "windowMinutes": 10080,
             "usedPercent": 41.5, "resetsAt": 1_800_500_000_000},
            {"id": "codex_spark:primary", "limit": "Spark", "windowMinutes": 10080,
             "usedPercent": 5.0, "resetsAt": 1_800_600_000_000},
        ],
        "limitReached": None,
        "credits": {"hasCredits": True, "unlimited": False, "balance": "12.5"},
        # Only unused resets, soonest expiry first.
        "resets": [
            {"title": "Full reset", "description": "", "grantedAt": 1_700_000_000_000, "expiresAt": 1_850_000_000_000},
            {"title": "Full reset", "description": "", "grantedAt": 1_700_000_000_000, "expiresAt": 1_900_000_000_000},
        ],
    }]


def test_missing_codex_is_reported_not_raised(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_codex_executable", lambda search_path: None)

    [plan] = plan_limits.read_limits({"claude": False, "codex": True})["plans"]

    assert plan["status"] == "missing"


def test_silent_codex_times_out(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "codex"
    script.write_text(f"#!{sys.executable}\nimport sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(plan_limits, "_codex_executable", lambda search_path: str(script))
    monkeypatch.setattr(plan_limits, "CODEX_TIMEOUT_S", 0.5)

    [plan] = plan_limits.read_limits({"claude": False, "codex": True})["plans"]

    assert plan["status"] == "failed"
    assert "did not answer" in plan["message"]


def test_claude_needs_the_status_line_until_it_reports(home: Path) -> None:
    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]
    assert plan["status"] == "setup"

    [plan] = plan_limits.read_limits({"claude": True, "codex": False}, statusline=True)["plans"]
    assert plan["status"] == "waiting"


def test_statusline_records_claude_limits(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "model": {"display_name": "Opus"},
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": 1_800_000_000},
            "seven_day": {"used_percentage": 41.2, "resets_at": 1_800_500_000},
        },
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert cli.main(["statusline"]) == 0
    assert capsys.readouterr().out == "5h 76% left · 7d 59% left\n"
    stored = home / ".config" / "agent-hub" / "claude-limits.json"
    assert stored.stat().st_mode & 0o777 == 0o600

    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]
    assert plan["status"] == "ok"
    assert plan["windows"] == [
        {"id": "five_hour", "windowMinutes": 300, "usedPercent": 23.5, "resetsAt": 1_800_000_000_000},
        {"id": "seven_day", "windowMinutes": 10080, "usedPercent": 41.2, "resetsAt": 1_800_500_000_000},
    ]


@pytest.mark.parametrize("stdin", ["not json", json.dumps({"model": {}})])
def test_statusline_never_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))

    assert cli.main(["statusline"]) == 0
    assert capsys.readouterr().out == "\n"
    assert not (home / ".config" / "agent-hub" / "claude-limits.json").exists()


def test_limits_endpoint_follows_usage_sources(server: str, home: Path, fake_codex: Path) -> None:
    settings = home / ".config" / "agent-hub" / "usage.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"claude": False, "codex": True}), encoding="utf-8")

    with urllib.request.urlopen(f"{server}/api/usage/limits", timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert [plan["provider"] for plan in payload["plans"]] == ["codex"]
    assert payload["plans"][0]["plan"] == "prolite"


def claude_store(content: Path) -> Path:
    write(content / "hub.toml", HUB_TOML.replace('enabled = ["claude"]', 'enabled = ["claude-code"]'))
    return content


def test_turning_on_the_status_line_applies_it_and_keeps_other_settings(
    server: str, content: Path, home: Path
) -> None:
    claude_store(content)
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    assert plan_limits.statusline_checks(config.load_machine_projection(content)) == []

    request = urllib.request.Request(
        f"{server}/api/config", method="POST", headers=SAME_ORIGIN,
        data=json.dumps({"action": "claude-statusline", "enabled": True}).encode(),
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        report = json.loads(response.read())

    assert report["exit_code"] == 0
    assert "[usage]\nclaude_statusline = true\n" in (content / "hub.toml").read_text()
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["statusLine"]["type"] == "command"
    assert data["statusLine"]["command"].endswith(" statusline")
    [check] = plan_limits.statusline_checks(config.load_machine_projection(content))
    assert check.level == "ok"
    with urllib.request.urlopen(f"{server}/api/usage/limits", timeout=20) as response:
        plans = json.loads(response.read())["plans"]
    assert plans[0] == {**plans[0], "provider": "claude", "status": "waiting"}


def test_status_reports_a_missing_status_line_and_never_replaces_another(content: Path, home: Path) -> None:
    claude_store(content)
    (content / "hub.toml").write_text(
        (content / "hub.toml").read_text() + "\n[usage]\nclaude_statusline = true\n", encoding="utf-8"
    )
    projection = config.load_machine_projection(content)
    [check] = plan_limits.statusline_checks(projection)
    assert check.level == "MISSING"

    settings = home / ".claude" / "settings.json"
    own = {"statusLine": {"type": "command", "command": "~/bin/my-agent-hub statusline"}}
    settings.write_text(json.dumps(own), encoding="utf-8")
    # Another status line is a warning, so Apply and Sync still succeed.
    report = operations.ContentOperations(content).apply()
    assert report.exit_code == 0
    assert [check.level for check in report.checks if check.kind == "statusline"] == ["warn"]
    assert json.loads(settings.read_text(encoding="utf-8")) == own

    settings.write_text("{not json", encoding="utf-8")
    [check] = plan_limits.statusline_checks(projection, apply=True)
    assert check.level == "DRIFT"


def test_usage_brings_the_newest_claude_limits_from_other_machines(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage_fleet._CACHE.clear()
    local = {
        "days": 30, "timeZone": "UTC", "sinceDay": "2026-10-01", "untilDay": "2026-10-26",
        "resolution": "day", "readAt": "2026-10-26T10:00:00Z", "buckets": [], "sources": [],
        "rollups": usage._build_rollups([], [], "day"),
    }
    monkeypatch.setattr(usage, "load_settings", lambda: {"claude": True, "codex": True, "grok": False, "cursor": False})
    monkeypatch.setattr(usage, "read_summary", lambda *_args: copy.deepcopy(local))
    targets = {name: remote.RemoteTarget(f"test@{name}", "/bin/agent-hub", "/store") for name in ("old", "new", "bad")}
    monkeypatch.setattr(remote, "load_remotes", lambda: targets)
    limits = {"five_hour": {"used_percentage": 10, "resets_at": 1_800_000_000}}
    targets["empty"] = targets["infinite"] = targets["bad"]
    records = {
        "old": {"capturedAt": 1, "rateLimits": limits},
        "new": {"capturedAt": 2, "rateLimits": limits},
        "bad": {"capturedAt": "3", "rateLimits": limits},
        # Newer, but with nothing usable: never shown over a real reading.
        "empty": {"capturedAt": 4, "rateLimits": {}},
        "infinite": {"capturedAt": 5, "rateLimits": {"five_hour": {"used_percentage": float("inf")}}},
    }
    monkeypatch.setattr(
        remote, "read_usage",
        lambda machine, *_args: {**copy.deepcopy(local), "claudeLimits": records[machine]},
    )

    result = usage_fleet.read_summary(30, "UTC")

    assert result["claudeLimits"]["machine"] == "new"
    assert result["claudeLimits"]["windows"][0]["usedPercent"] == 10.0


def t3_log(home: Path, name: str, lines: list[str]) -> Path:
    path = home / ".t3" / "userdata" / "logs" / "provider" / f"events.{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def rate_limit_line(stamp: str, five: float, week: float) -> str:
    event = {"event": {"payload": {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": five, "resetsAt": 1_800_000_000},
            "seven_day": {"utilization": week, "resetsAt": 1_800_500_000},
        },
    }}}}
    return f"[{stamp}] NTIVE: {json.dumps(event)}"


def test_claude_limits_come_from_t3_code_without_any_setup(home: Path) -> None:
    mention = json.dumps({"event": {"text": '"type":"rate_limit_event" in a tool result'}})
    t3_log(home, "thread", [
        rate_limit_line("2026-10-26T10:00:00.000Z", 0.10, 0.05),
        rate_limit_line("2026-10-26T11:05:06.874Z", 0.29, 0.08),
        f"[2026-10-26T11:06:00.000Z] NTIVE: {mention}",
        "[2026-10-26T11:07:00.000Z] NTIVE: {broken",
    ])

    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]

    assert plan["status"] == "ok" and plan["source"] == "t3"
    assert plan["capturedAt"] == 1_793_012_706_874
    assert [(window["id"], round(window["usedPercent"], 6)) for window in plan["windows"]] == [
        ("five_hour", 29.0), ("seven_day", 8.0),
    ]


def test_the_newer_of_t3_code_and_the_status_line_wins(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t3_log(home, "thread", [rate_limit_line("2000-01-01T00:00:00.000Z", 0.5, 0.5)])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 1_800_000_000}},
    })))
    assert cli.main(["statusline"]) == 0

    # The status line reading is newer than the one in the T3 Code log.
    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]
    assert plan["source"] == "statusline"

    plan_limits.clear_cache()
    t3_log(home, "thread", [rate_limit_line("2999-01-01T00:00:00.000Z", 0.5, 0.5)])
    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]
    assert plan["source"] == "t3"


def test_the_newest_t3_reading_wins_across_parallel_threads(home: Path) -> None:
    newer = t3_log(home, "a", [rate_limit_line("2026-10-26T11:00:00.000Z", 0.4, 0.1)])
    busy = t3_log(home, "b", [rate_limit_line("2026-10-26T10:00:00.000Z", 0.2, 0.1), "[2026-10-26T11:30:00.000Z] NTIVE: {}"])
    os.utime(newer, (1_000, time.time() - 60))
    os.utime(busy, (1_000, time.time()))

    [plan] = plan_limits.read_limits({"claude": True, "codex": False})["plans"]

    assert round(plan["windows"][0]["usedPercent"]) == 40
