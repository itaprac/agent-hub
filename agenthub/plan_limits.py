"""Read the subscription plan limits that the provider CLIs report themselves.

Codex: ``codex app-server`` answers ``account/rateLimits/read`` with the
plan, each window's used percentage and reset time, credits, and banked
rate limit resets. Claude: Claude Code passes ``rate_limits`` to its status
line command; ``agent-hub statusline`` stores the latest copy so the Console
can show it. agent-hub never reads or sends either tool's credentials.
"""

from __future__ import annotations

import copy
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import fileio

CACHE_TTL_S = 60
CODEX_TIMEOUT_S = 15
CLAUDE_WINDOWS = {"five_hour": 300, "seven_day": 10080}
STATUSLINE_COMMAND = "agent-hub statusline"
CLAUDE_SETUP = (
    'Set "statusLine": {"type": "command", "command": "agent-hub statusline"} '
    "in ~/.claude/settings.json, then send one message in Claude Code."
)

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _claude_path() -> Path:
    return Path.home() / ".config" / "agent-hub" / "claude-limits.json"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _epoch_ms(value: Any) -> int | None:
    seconds = _number(value)
    return int(seconds * 1000) if seconds is not None and seconds > 0 else None


# ------------------------------------------------------------------ Claude


def record_claude_status(payload: Any) -> dict[str, Any] | None:
    """Store the status line's ``rate_limits``; return what was stored."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rate_limits"), dict):
        return None
    limits = payload["rate_limits"]
    path = _claude_path()
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    now = _now_ms()
    # The status line runs after every message; skip identical rewrites.
    if (
        isinstance(previous, dict)
        and previous.get("rateLimits") == limits
        and isinstance(previous.get("capturedAt"), int)
        and now - previous["capturedAt"] < CACHE_TTL_S * 1000
    ):
        return previous
    record = {"capturedAt": now, "rateLimits": limits}
    fileio.atomic_write(path, (json.dumps(record) + "\n").encode("utf-8"), 0o600)
    return record


def statusline_text(record: dict[str, Any] | None) -> str:
    limits = record.get("rateLimits") if isinstance(record, dict) else None
    if not isinstance(limits, dict):
        return ""
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = limits.get(key)
        used = _number(window.get("used_percentage")) if isinstance(window, dict) else None
        if used is not None:
            parts.append(f"{label} {used:.0f}%")
    return " · ".join(parts)


def _claude_plan() -> dict[str, Any]:
    plan: dict[str, Any] = {"provider": "claude", "windows": []}
    path = _claude_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {**plan, "status": "setup", "message": CLAUDE_SETUP}
    except (OSError, ValueError) as exc:
        return {**plan, "status": "failed", "message": f"Cannot read {path}: {exc}"}
    limits = record.get("rateLimits") if isinstance(record, dict) else None
    if not isinstance(limits, dict):
        return {**plan, "status": "failed", "message": f"{path} has no rate limits"}
    windows = []
    for key, minutes in CLAUDE_WINDOWS.items():
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        used = _number(window.get("used_percentage"))
        if used is None:
            continue
        windows.append({
            "id": key, "windowMinutes": minutes, "usedPercent": used,
            "resetsAt": _epoch_ms(window.get("resets_at")),
        })
    captured = record.get("capturedAt")
    return {
        **plan, "status": "ok", "windows": windows,
        "capturedAt": captured if isinstance(captured, int) else None,
    }


# ------------------------------------------------------------------- Codex


def _codex_search_path() -> str:
    home = Path.home()
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    parts += [str(home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(parts))


def _codex_executable(search_path: str) -> str | None:
    return shutil.which("codex", path=search_path)


def _codex_request(method: str) -> dict[str, Any]:
    """Run one JSON-RPC request against a short-lived ``codex app-server``."""
    search_path = _codex_search_path()
    executable = _codex_executable(search_path)
    if not executable:
        raise FileNotFoundError("codex is not on PATH")
    process = subprocess.Popen(
        [executable, "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env={**os.environ, "PATH": search_path},
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + CODEX_TIMEOUT_S

    def send(message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def answer(request_id: int) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("codex app-server did not answer in time")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("codex app-server exited before answering")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if isinstance(message.get("error"), dict):
                raise RuntimeError(str(message["error"].get("message") or "codex app-server error"))
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("codex app-server returned no result")
            return result

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent-hub", "title": "agent-hub", "version": "1"},
        }})
        answer(1)
        send({"jsonrpc": "2.0", "method": "initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": method})
        return answer(2)
    finally:
        process.kill()
        process.wait(timeout=5)


def _codex_windows(snapshot: dict[str, Any], limit: str | None) -> list[dict[str, Any]]:
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict):
            continue
        used = _number(window.get("usedPercent"))
        if used is None:
            continue
        minutes = _number(window.get("windowDurationMins"))
        windows.append({
            "id": f"{snapshot.get('limitId') or 'codex'}:{key}",
            "limit": limit,
            "windowMinutes": int(minutes) if minutes else None,
            "usedPercent": used,
            "resetsAt": _epoch_ms(window.get("resetsAt")),
        })
    return windows


def _codex_plan_from(result: dict[str, Any]) -> dict[str, Any]:
    raw_main = result.get("rateLimits")
    main: dict[str, Any] = raw_main if isinstance(raw_main, dict) else {}
    main_id = main.get("limitId")
    windows = _codex_windows(main, None)
    others = result.get("rateLimitsByLimitId")
    if isinstance(others, dict):
        for limit_id, snapshot in others.items():
            if limit_id != main_id and isinstance(snapshot, dict):
                windows += _codex_windows(snapshot, str(snapshot.get("limitName") or limit_id))
    credits = main.get("credits") if isinstance(main.get("credits"), dict) else None
    resets_raw = result.get("rateLimitResetCredits")
    resets = []
    if isinstance(resets_raw, dict) and isinstance(resets_raw.get("credits"), list):
        for item in resets_raw["credits"]:
            if isinstance(item, dict) and item.get("status") == "available":
                resets.append({
                    "title": str(item.get("title") or "Rate limit reset"),
                    "description": str(item.get("description") or ""),
                    "grantedAt": _epoch_ms(item.get("grantedAt")),
                    "expiresAt": _epoch_ms(item.get("expiresAt")),
                })
    resets.sort(key=lambda item: item["expiresAt"] or 0)
    return {
        "provider": "codex", "status": "ok", "capturedAt": _now_ms(),
        "plan": main.get("planType") if isinstance(main.get("planType"), str) else None,
        "windows": windows,
        "limitReached": main.get("rateLimitReachedType") or None,
        "credits": {
            "hasCredits": credits.get("hasCredits") is True,
            "unlimited": credits.get("unlimited") is True,
            "balance": str(credits.get("balance") or "0"),
        } if credits else None,
        "resets": resets,
    }


def _codex_plan() -> dict[str, Any]:
    try:
        return _codex_plan_from(_codex_request("account/rateLimits/read"))
    except FileNotFoundError:
        return {"provider": "codex", "status": "missing", "windows": [],
                "message": "The codex CLI is not installed on this Machine."}
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        return {"provider": "codex", "status": "failed", "windows": [],
                "message": f"Codex did not report its limits: {exc}"}


# ------------------------------------------------------------------ public


def _cached(name: str, read: Any) -> dict[str, Any]:
    with _LOCK:
        hit = _CACHE.get(name)
        if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
            return copy.deepcopy(hit[1])
        result = read()
        _CACHE[name] = (time.monotonic(), result)
        return copy.deepcopy(result)


def read_limits(settings: dict[str, Any]) -> dict[str, Any]:
    """Plan limits for the enabled Usage sources that report them."""
    plans = []
    if settings.get("claude", True):
        plans.append(_claude_plan())
    if settings.get("codex", True):
        plans.append(_cached("codex", _codex_plan))
    return {"readAt": _now_ms(), "plans": plans}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
