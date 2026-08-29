#!/usr/bin/env python3
"""Unit checks for usage parsers and optional-source settings."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agenthub import usage  # noqa: E402


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _claude_record(message_id: str, input_tokens: int = 1, reported_cost: float | None = 0.01) -> str:
    record = {
        "type": "assistant",
        "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "sessionId": "session-1",
        "message": {
            "id": message_id,
            "model": "claude-test",
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }
    if reported_cost is not None:
        record["costUSD"] = reported_cost
    return json.dumps(record)


def _record_count(summary: dict) -> int:
    return sum(bucket["records"] for bucket in summary["buckets"])


def _keep_transcript_metadata(monkeypatch, transcript: Path) -> None:
    metadata = transcript.stat()
    real_stat = Path.stat

    def stable_stat(path: Path, *args, **kwargs):
        if path == transcript:
            return metadata
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stable_stat)


def _disabled_sources() -> dict[str, bool]:
    return {"claude": False, "codex": False, "grok": False, "cursor": False}


def test_summary_uses_host_time_zone_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_host_time_zone_name", lambda: "Europe/London")
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)

    summary = usage.read_summary(days=7, settings=_disabled_sources())

    assert summary["timeZone"] == "Europe/London"


def test_summary_falls_back_to_utc_without_host_zone(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_host_time_zone_name", lambda: None)
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)

    summary = usage.read_summary(days=7, settings=_disabled_sources())

    assert summary["timeZone"] == "UTC"


def test_summary_honors_valid_time_zone_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_host_time_zone_name", lambda: "UTC")
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)

    summary = usage.read_summary(
        days=7, time_zone="Asia/Tokyo", settings=_disabled_sources()
    )

    assert summary["timeZone"] == "Asia/Tokyo"


def test_read_summary_reuses_snapshot_until_ttl(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)
    monkeypatch.setattr(usage, "_rates_state", lambda: ("unavailable", 0, 0))
    clock = [100.0]
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    transcript = tmp_path / ".claude" / "projects" / "demo" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_claude_record("message-1") + "\n", encoding="utf-8")
    settings = {"claude": True, "codex": False, "grok": False, "cursor": False}

    cold = usage.read_summary(days=7, time_zone="UTC", settings=settings)
    transcript.write_text(
        _claude_record("message-1") + "\n" + _claude_record("message-2") + "\n",
        encoding="utf-8",
    )
    clock[0] += 59
    warm = usage.read_summary(days=7, time_zone="UTC", settings=settings)
    clock[0] += 2
    expired = usage.read_summary(days=7, time_zone="UTC", settings=settings)

    assert_eq(_record_count(cold), 1, "cold snapshot")
    assert_eq(_record_count(warm), 1, "warm snapshot")
    assert_eq(_record_count(expired), 2, "expired snapshot")


def test_unchanged_transcript_uses_memo_after_snapshot_expiry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)
    monkeypatch.setattr(usage, "_rates_state", lambda: ("unavailable", 0, 0))
    clock = [150.0]
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    transcript = tmp_path / ".claude" / "projects" / "demo" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_claude_record("message-1") + "\n", encoding="utf-8")
    _keep_transcript_metadata(monkeypatch, transcript)
    settings = {"claude": True, "codex": False, "grok": False, "cursor": False}

    cold = usage.read_summary(days=7, time_zone="UTC", settings=settings)
    transcript.write_text("invalid JSON that the memo must not read\n", encoding="utf-8")
    clock[0] += 61
    warm_scan = usage.read_summary(days=7, time_zone="UTC", settings=settings)

    assert_eq(_record_count(cold), 1, "cold Transcript scan")
    assert_eq(_record_count(warm_scan), 1, "warm Transcript scan")


def test_transcript_memo_evicts_entries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_ensure_rates_refresh", lambda: None)
    monkeypatch.setattr(usage, "_rates_state", lambda: ("unavailable", 0, 0))
    monkeypatch.setattr(usage, "FILE_CACHE_MAX_ENTRIES", 0, raising=False)
    clock = [200.0]
    monkeypatch.setattr(usage.time, "monotonic", lambda: clock[0])
    transcript = tmp_path / ".claude" / "projects" / "demo" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_claude_record("message-1", input_tokens=1) + "\n", encoding="utf-8")
    _keep_transcript_metadata(monkeypatch, transcript)
    settings = {"claude": True, "codex": False, "grok": False, "cursor": False}

    first = usage.read_summary(days=7, time_zone="UTC", settings=settings)
    transcript.write_text(_claude_record("message-1", input_tokens=9) + "\n", encoding="utf-8")
    clock[0] += 61
    after_eviction = usage.read_summary(days=7, time_zone="UTC", settings=settings)

    assert_eq(first["buckets"][0]["totals"]["uncachedInputTokens"], 1, "first scan")
    assert_eq(after_eviction["buckets"][0]["totals"]["uncachedInputTokens"], 9, "scan after eviction")


def test_rate_failure_does_not_block_summary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_RATES", {})
    monkeypatch.setattr(usage, "_RATES_AT", 0.0)
    monkeypatch.setattr(usage, "_RATES_STATUS", "unavailable")
    fetch_started = threading.Event()
    fetch_finished = threading.Event()
    release_fetch = threading.Event()
    fetches = [0]

    def blocked_fetch(*_args, **_kwargs):
        fetches[0] += 1
        fetch_started.set()
        release_fetch.wait(timeout=2)
        fetch_finished.set()
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(usage.urllib.request, "urlopen", blocked_fetch)
    settings = {"claude": False, "codex": False, "grok": False, "cursor": False}

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(usage.read_summary, 90, "UTC", settings)
        try:
            assert fetch_started.wait(timeout=0.5), "rate refresh did not start"
            summary = future.result(timeout=0.5)
        finally:
            release_fetch.set()

    assert fetch_finished.wait(timeout=0.5), "rate refresh did not finish"
    usage.read_summary(30, "UTC", settings)
    assert_eq(summary["pricing"]["status"], "unavailable", "pricing failure status")
    assert_eq(fetches[0], 1, "rate refresh retry bound")


def test_rate_thread_start_failure_does_not_block_summary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    def fail_start(_thread) -> None:
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(usage.threading.Thread, "start", fail_start)
    settings = {"claude": False, "codex": False, "grok": False, "cursor": False}

    summary = usage.read_summary(days=7, time_zone="UTC", settings=settings)

    assert_eq(summary["buckets"], [], "summary after rate thread failure")
    assert_eq(summary["pricing"]["status"], "unavailable", "pricing after rate thread failure")


def test_summary_uses_one_rate_generation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rates = tmp_path / ".cache" / "agent-hub" / "usage-model-rates.json"
    rates.parent.mkdir(parents=True)
    rates.write_text(
        json.dumps(
            {
                "fetchedAt": 1,
                "document": {
                    "claude-test": {
                        "input_cost_per_token": 1e-6,
                        "output_cost_per_token": 1e-6,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    transcripts = tmp_path / ".claude" / "projects" / "demo"
    transcripts.mkdir(parents=True)
    for index in range(2):
        (transcripts / f"session-{index}.jsonl").write_text(
            _claude_record(f"message-{index}", reported_cost=None) + "\n",
            encoding="utf-8",
        )
    second_file_opened = threading.Event()
    rate_refresh_finished = threading.Event()
    real_open = Path.open
    real_write_text = Path.write_text
    transcript_opens = [0]

    class RateResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            assert second_file_opened.wait(timeout=2), "second Transcript was not opened"
            return json.dumps(
                {
                    "claude-test": {
                        "input_cost_per_token": 10e-6,
                        "output_cost_per_token": 10e-6,
                    }
                }
            ).encode("utf-8")

    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *_args, **_kwargs: RateResponse())

    def tracked_write_text(path: Path, *args, **kwargs):
        result = real_write_text(path, *args, **kwargs)
        if path == rates:
            rate_refresh_finished.set()
        return result

    def coordinated_open(path: Path, *args, **kwargs):
        if path.parent == transcripts:
            transcript_opens[0] += 1
            if transcript_opens[0] == 2:
                second_file_opened.set()
                assert rate_refresh_finished.wait(timeout=2), "rate refresh did not finish"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracked_write_text)
    monkeypatch.setattr(Path, "open", coordinated_open)
    settings = {"claude": True, "codex": False, "grok": False, "cursor": False}

    summary = usage.read_summary(days=7, time_zone="UTC", settings=settings)

    assert_eq(summary["buckets"][0]["costUsd"], 0.000004, "single rate generation")
    assert_eq(summary["pricing"]["status"], "cached", "captured rate status")


def test_concurrent_summaries_are_consistent_and_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rates = tmp_path / ".cache" / "agent-hub" / "usage-model-rates.json"
    rates.parent.mkdir(parents=True)
    rates.write_text(
        json.dumps(
            {
                "fetchedAt": usage.time.time(),
                "document": {
                    "claude-test": {
                        "input_cost_per_token": 1e-6,
                        "output_cost_per_token": 2e-6,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    disabled = {"claude": False, "codex": False, "grok": False, "cursor": False}
    usage.read_summary(days=90, time_zone="UTC", settings=disabled)
    transcript = tmp_path / ".claude" / "projects" / "demo" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_claude_record("message-1") + "\n", encoding="utf-8")
    settings = {"claude": True, "codex": False, "grok": False, "cursor": False}
    time_zones = ["UTC", "Europe/Warsaw", "Europe/London", "Etc/GMT+1"]
    start = threading.Barrier(len(time_zones))
    file_reads = threading.Barrier(len(time_zones))
    real_open = Path.open

    def concurrent_open(path: Path, *args, **kwargs):
        if path == transcript:
            file_reads.wait(timeout=2)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", concurrent_open)

    def read(time_zone: str) -> dict:
        start.wait(timeout=2)
        return usage.read_summary(days=7, time_zone=time_zone, settings=settings)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(time_zones)) as executor:
        summaries = list(executor.map(read, time_zones))

    assert_eq([_record_count(summary) for summary in summaries], [1] * len(time_zones), "concurrent summaries")
    assert_eq([summary["pricing"]["knownModels"] for summary in summaries], [1] * len(time_zones), "shared rates")
    summaries[0]["buckets"].clear()
    assert_eq(_record_count(usage.read_summary(7, "UTC", settings)), 1, "isolated cached summary")


def test_grok_turn_completed() -> None:
    line = json.dumps(
        {
            "timestamp": 1786706841,
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "turn_completed",
                    "prompt_id": "prompt-1",
                    "usage": {
                        "inputTokens": 202035,
                        "outputTokens": 5765,
                        "cachedReadTokens": 165248,
                        "cacheCreationTokens": 0,
                        "reasoningTokens": 4224,
                        "costUsdTicks": 1907880000,
                        "modelUsage": {
                            "grok-4.6-build": {
                                "inputTokens": 202035,
                                "outputTokens": 5765,
                                "cachedReadTokens": 165248,
                                "cacheCreationTokens": 0,
                                "reasoningTokens": 4224,
                                "costUsdTicks": 1907880000,
                            }
                        },
                    },
                },
            },
        }
    )
    records = usage._parse_grok_line(line)
    assert_eq(len(records), 1, "grok record count")
    record = records[0]
    assert_eq(record["provider"], "grok", "provider")
    assert_eq(record["model"], "grok-4.6-build", "model")
    assert_eq(record["timestampMs"], 1786706841000, "timestamp")
    assert_eq(record["totals"]["uncachedInputTokens"], 36787, "uncached")
    assert_eq(record["totals"]["cachedInputTokens"], 165248, "cached")
    assert_eq(record["totals"]["outputTokens"], 5765, "output")
    assert_eq(round(record["reportedCostUsd"], 6), 0.190788, "ticks to USD")


def test_cursor_event() -> None:
    record = usage._parse_cursor_event(
        {
            "timestamp": "1760921734850",
            "model": "grok-4.6",
            "usageBasedCosts": "$1.25",
            "requestId": "req-1",
            "tokenUsage": {
                "inputTokens": 12,
                "outputTokens": 38,
                "cacheReadTokens": 13630,
            },
        }
    )
    assert record is not None
    assert_eq(record["provider"], "cursor", "provider")
    assert_eq(record["totals"]["uncachedInputTokens"], 12, "uncached")
    assert_eq(record["totals"]["cachedInputTokens"], 13630, "cached")
    assert_eq(record["reportedCostUsd"], 1.25, "reported cost")


def test_settings_round_trip() -> None:
    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            defaults = usage.public_settings()
            assert_eq(defaults["claude"], True, "claude on by default")
            assert_eq(defaults["codex"], True, "codex on by default")
            assert_eq(defaults["grok"], False, "grok off by default")
            saved = usage.save_settings({"claude": False, "grok": True, "cursor": True, "cursorToken": "sess-token"})
            assert_eq(saved["claude"], False, "claude disabled")
            assert_eq(saved["grok"], True, "grok enabled")
            assert_eq(saved["cursorTokenSet"], True, "token set")
            token_path = home / ".config" / "agent-hub" / "cursor-session-token"
            mode = token_path.stat().st_mode & 0o777
            assert_eq(mode, 0o600, "token mode")
            public = usage.public_settings()
            assert_eq(public["cursorTokenSet"], True, "public token flag")
            assert "cursorToken" not in public, "token stays private"
            usage.save_settings({"cursorToken": ""})
            assert_eq(usage.public_settings()["cursorTokenSet"], False, "token cleared")
            before = (home / ".config" / "agent-hub" / "usage.json").read_text(encoding="utf-8")
            try:
                usage.save_settings({"grok": False, "cursorToken": 42})
            except ValueError:
                pass
            else:
                raise AssertionError("invalid token type was accepted")
            after = (home / ".config" / "agent-hub" / "usage.json").read_text(encoding="utf-8")
            assert_eq(after, before, "invalid token does not partially save flags")
            try:
                usage.save_settings({"grok": "false"})
            except ValueError:
                pass
            else:
                raise AssertionError("non-boolean source flag was accepted")
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old


def test_read_summary_includes_grok_only_when_enabled() -> None:
    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        recent_timestamp = int(datetime.now(timezone.utc).timestamp()) - 60
        session = home / ".grok" / "sessions" / "proj" / "abc"
        session.mkdir(parents=True)
        (session / "updates.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": recent_timestamp,
                    "params": {
                        "sessionId": "abc",
                        "update": {
                            "sessionUpdate": "turn_completed",
                            "prompt_id": "p1",
                            "usage": {
                                "inputTokens": 100,
                                "outputTokens": 10,
                                "cachedReadTokens": 20,
                                "modelUsage": {"grok-4.6-build": {"inputTokens": 100, "outputTokens": 10, "cachedReadTokens": 20}},
                            },
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(session / "updates.jsonl", None)
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            off = usage.read_summary(
                days=90,
                settings={"claude": False, "codex": True, "grok": False, "cursor": False, "cursorToken": ""},
            )
            assert_eq([s["provider"] for s in off["sources"]], ["codex"], "disabled claude omitted")
            on = usage.read_summary(days=90, settings={"grok": True, "cursor": False, "cursorToken": ""})
            providers = [s["provider"] for s in on["sources"]]
            if "grok" not in providers:
                raise AssertionError(f"expected grok source, got {providers}")
            grok_buckets = [b for b in on["buckets"] if b["provider"] == "grok"]
            if not grok_buckets:
                raise AssertionError("expected a grok bucket from the fixture session")
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old


def test_merge_keeps_one_cursor_source() -> None:
    left = {
        "machine": "mini",
        "buckets": [{"day": "2026-08-01", "hourStart": None, "provider": "cursor", "model": "a", "totals": usage._empty_totals(), "costUsd": 1, "cacheSavingsUsd": 0, "records": 1, "unpricedRecords": 0, "sessions": 1}],
        "sources": [{"provider": "cursor", "status": "ok", "scannedFiles": 2, "sessions": 1, "machine": "mini"}],
        "settings": {"grok": False, "cursor": True, "cursorTokenSet": True},
    }
    right = {
        "machine": "macbook",
        "buckets": [{"day": "2026-08-01", "hourStart": None, "provider": "cursor", "model": "a", "totals": usage._empty_totals(), "costUsd": 4, "cacheSavingsUsd": 0, "records": 1, "unpricedRecords": 0, "sessions": 1}],
        "sources": [{"provider": "cursor", "status": "ok", "scannedFiles": 8, "sessions": 1, "machine": "macbook"}],
    }
    merged = usage.merge_summaries([left, right])
    assert_eq(len(merged["buckets"]), 1, "one cursor bucket")
    assert_eq(merged["buckets"][0]["costUsd"], 1.0, "keep first machine")
    assert_eq([s["machine"] for s in merged["sources"]], ["mini"], "one cursor source")


def test_merge_returns_finished_rollups() -> None:
    monday = {
        "day": "2026-08-03",
        "hourStart": None,
        "provider": "claude",
        "model": "claude-opus-5",
        "totals": {
            "uncachedInputTokens": 10,
            "cachedInputTokens": 20,
            "cacheCreationTokens": 1,
            "outputTokens": 5,
            "reasoningTokens": 2,
        },
        "costUsd": 1.25,
        "cacheSavingsUsd": 0.5,
        "records": 2,
        "unpricedRecords": 0,
        "sessions": 1,
    }
    mini = {
        "machine": "mini",
        "resolution": "day",
        "buckets": [monday],
        "sources": [
            {
                "provider": "claude",
                "status": "ok",
                "scannedFiles": 1,
                "sessions": 1,
                "machine": "mini",
            }
        ],
        "settings": usage.public_settings({}),
    }
    macbook = {
        "machine": "macbook",
        "resolution": "day",
        "buckets": [
            {
                **monday,
                "provider": "codex",
                "model": "gpt-5.6",
                "totals": {
                    "uncachedInputTokens": 20,
                    "cachedInputTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 10,
                    "reasoningTokens": 4,
                },
                "costUsd": 2.0,
                "cacheSavingsUsd": 0.0,
                "records": 1,
            },
            {
                **monday,
                "day": "2026-08-10",
                "totals": {
                    "uncachedInputTokens": 5,
                    "cachedInputTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 5,
                    "reasoningTokens": 1,
                },
                "costUsd": 0.5,
                "cacheSavingsUsd": 0.0,
                "records": 1,
            },
        ],
        "sources": [
            {
                "provider": "codex",
                "status": "ok",
                "scannedFiles": 1,
                "sessions": 1,
                "machine": "macbook",
            },
            {
                "provider": "claude",
                "status": "ok",
                "scannedFiles": 1,
                "sessions": 1,
                "machine": "macbook",
            },
        ],
    }

    report = usage.merge_summaries([mini, macbook])["rollups"]

    assert_eq(report["total"]["totalTokens"], 76, "total tokens")
    assert_eq(report["total"]["costUsd"], 3.75, "total cost")
    assert_eq(report["total"]["sessions"], 3, "total sessions")
    assert_eq(
        [(row["day"], row["totalTokens"]) for row in report["daily"]],
        [("2026-08-03", 66), ("2026-08-10", 10)],
        "daily rollups",
    )
    assert_eq(
        [(row["weekStart"], row["totalTokens"]) for row in report["weekly"]],
        [("2026-08-03", 66), ("2026-08-10", 10)],
        "weekly rollups",
    )
    assert_eq(
        [(row["source"], row["model"], row["totalTokens"]) for row in report["byModel"]],
        [("codex", "gpt-5.6", 30), ("claude", "claude-opus-5", 46)],
        "model rollups",
    )
    assert_eq(
        [(row["machine"], row["totalTokens"], row["sessions"]) for row in report["byMachine"]],
        [("mini", 36, 1), ("macbook", 40, 2)],
        "machine rollups",
    )
    assert_eq(
        [(row["source"], row["totalTokens"], row["sessions"]) for row in report["bySource"]],
        [("claude", 46, 2), ("codex", 30, 1)],
        "source rollups",
    )


def test_rate_table_prefers_bare_entry_over_reseller() -> None:
    # LiteLLM lists resellers under the same normalized key, often without
    # cache rates; those must not overwrite the first-party entry.
    table = usage._parse_rate_table(
        {
            "claude-opus-5": {
                "input_cost_per_token": 5e-06,
                "output_cost_per_token": 2.5e-05,
                "cache_read_input_token_cost": 5e-07,
                "cache_creation_input_token_cost": 6.25e-06,
            },
            "deepinfra/anthropic/claude-opus-5": {
                "input_cost_per_token": 5e-06,
                "output_cost_per_token": 2.5e-05,
            },
            "openrouter/openai/gpt-x": {
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 4e-06,
            },
            "azure/gpt-x": {
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 4e-06,
                "cache_read_input_token_cost": 1e-07,
            },
        }
    )
    assert_eq(table["claude-opus-5"]["cacheRead"], 5e-07, "bare entry wins")
    assert_eq(table["gpt-x"]["cacheRead"], 1e-07, "entry with cache rate wins")


def main() -> int:
    test_grok_turn_completed()
    test_cursor_event()
    test_settings_round_trip()
    test_read_summary_includes_grok_only_when_enabled()
    test_merge_keeps_one_cursor_source()
    test_rate_table_prefers_bare_entry_over_reseller()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
