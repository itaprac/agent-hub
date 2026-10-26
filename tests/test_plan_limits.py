"""Plan limits from Codex's app-server and Claude Code's status line."""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

from agenthub import cli, plan_limits

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
    assert "agent-hub statusline" in plan["message"]


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
    assert capsys.readouterr().out == "5h 24% · 7d 41%\n"
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
