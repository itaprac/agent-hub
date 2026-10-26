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
import math
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import fileio

CACHE_TTL_S = 60
CODEX_TIMEOUT_S = 15
CLAUDE_WINDOWS = {"five_hour": 300, "seven_day": 10080}
STATUSLINE_COMMAND = "agent-hub statusline"
CLAUDE_SETUP = "Turn on the Claude Code status line to record plan limits."
CLAUDE_WAITING = (
    "The status line is on. Limits appear after the next Claude Code message; "
    "other Machines get the status line with their next Sync."
)

T3_TAIL_BYTES = 4 * 1024 * 1024
T3_MAX_FILES = 8
T3_MAX_AGE_S = 8 * 24 * 60 * 60

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_T3_CACHE: dict[str, tuple[tuple[str, int, int], dict[str, Any] | None]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _claude_path() -> Path:
    return Path.home() / ".config" / "agent-hub" / "claude-limits.json"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
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
            parts.append(f"{label} {max(0.0, 100 - used):.0f}% left")
    return " · ".join(parts)


def _statusline_record() -> dict[str, Any] | None:
    try:
        record = json.loads(_claude_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _t3_logs() -> Path:
    return Path.home() / ".t3" / "userdata" / "logs" / "provider"


def _rate_limit_info(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("type") == "rate_limit_event" and isinstance(value.get("rate_limit_info"), dict):
            return value["rate_limit_info"]
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _rate_limit_info(child)
        if found is not None:
            return found
    return None


def _last_t3_rate_limit(path: Path) -> dict[str, Any] | None:
    """The newest rate_limit_event that Claude Code sent to T3 Code in one log."""
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - T3_TAIL_BYTES))
        tail = handle.read()
    for line in reversed(tail.splitlines()):
        if b'"rate_limit_event"' not in line or not line.startswith(b"[") or b"{" not in line:
            continue
        try:
            stamp = datetime.fromisoformat(line[1:line.index(b"]")].decode().replace("Z", "+00:00"))
            info = _rate_limit_info(json.loads(line[line.index(b"{"):]))
        except (ValueError, UnicodeDecodeError):
            continue
        windows = info.get("unifiedWindows") if info else None
        if not isinstance(windows, dict):
            continue
        limits = {}
        for key in CLAUDE_WINDOWS:
            window = windows.get(key)
            if not isinstance(window, dict):
                continue
            share = _number(window.get("utilization"))
            if share is not None:
                limits[key] = {"used_percentage": share * 100, "resets_at": window.get("resetsAt")}
        if limits:
            return {"capturedAt": int(stamp.timestamp() * 1000), "source": "t3", "rateLimits": limits}
    return None


def _t3_record() -> dict[str, Any] | None:
    """Claude Code run by T3 Code has no status line, but logs the same limits."""
    cutoff = time.time() - T3_MAX_AGE_S
    try:
        logs = [(path, path.stat()) for path in _t3_logs().glob("events.*.log")]
    except OSError:
        return None
    logs = sorted((item for item in logs if item[1].st_mtime >= cutoff), key=lambda item: -item[1].st_mtime)
    # Threads log in parallel, so a newer file need not hold the newest reading.
    records = []
    for path, stat in logs[:T3_MAX_FILES]:
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with _LOCK:
            hit = _T3_CACHE.get(str(path))
        if hit is not None and hit[0] == key:
            record = hit[1]
        else:
            try:
                record = _last_t3_rate_limit(path)
            except OSError:
                continue
            with _LOCK:
                _T3_CACHE[str(path)] = (key, record)
        if record is not None:
            records.append(record)
    return copy.deepcopy(max(records, key=lambda record: record["capturedAt"])) if records else None


def read_claude_record() -> dict[str, Any] | None:
    """The newest Claude limits on this Machine, as sent to a controller; None if there are none."""
    records = [
        record for record in (_statusline_record(), _t3_record())
        if record is not None and isinstance(record.get("capturedAt"), int)
    ]
    return max(records, key=lambda record: record["capturedAt"]) if records else None


def claude_plan_from(record: Any) -> dict[str, Any] | None:
    """Normalize a status line record from this or another Machine."""
    limits = record.get("rateLimits") if isinstance(record, dict) else None
    captured = record.get("capturedAt") if isinstance(record, dict) else None
    if not isinstance(limits, dict) or isinstance(captured, bool) or not isinstance(captured, int):
        return None
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
    if not windows:
        return None
    source = "t3" if record.get("source") == "t3" else "statusline"
    return {"provider": "claude", "status": "ok", "windows": windows, "capturedAt": captured, "source": source}


def _claude_plan(statusline: bool) -> dict[str, Any]:
    plan = claude_plan_from(read_claude_record())
    if plan is not None:
        return plan
    if statusline:
        return {"provider": "claude", "status": "waiting", "windows": [], "message": CLAUDE_WAITING}
    return {"provider": "claude", "status": "setup", "windows": [], "message": CLAUDE_SETUP}


def newest_claude_plan(plans: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [plan for plan in plans if plan.get("status") == "ok"]
    return max(ready, key=lambda plan: plan["capturedAt"]) if ready else None


def _statusline_command() -> str:
    found = shutil.which("agent-hub")
    # Resolve symlinks so every caller on this Machine writes the same command.
    return f"{shlex.quote(str(Path(found).resolve()))} statusline" if found else STATUSLINE_COMMAND


def _ours(value: Any) -> bool:
    command = value.get("command") if isinstance(value, dict) else None
    try:
        words = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return False
    return len(words) == 2 and Path(words[0]).name == "agent-hub" and words[1] == "statusline"


def statusline_checks(projection: Any, *, apply: bool = False, dry_run: bool = False) -> list[Any]:
    """Status or Apply of the Claude Code status line that records plan limits."""
    from .core import StatusCheck

    agent = next((item for item in projection.agents if item.name == "claude-code"), None)
    if not projection.settings.get("claude_statusline") or agent is None or not agent.instructions_global:
        return []
    path = Path(agent.instructions_global).parent / "settings.json"
    fields = {"kind": "statusline", "agent": agent.name, "name": "statusline", "target": str(path)}
    label = f"{agent.name} plan limits status line: {path}"
    command = _statusline_command()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        return [StatusCheck(level="DRIFT", text=f"{label} cannot be read: {exc}", **fields)]
    if not isinstance(data, dict):
        return [StatusCheck(level="DRIFT", text=f"{label} is not a JSON object", **fields)]
    current = data.get("statusLine")
    if isinstance(current, dict) and current.get("type") == "command" and current.get("command") == command:
        return [StatusCheck(level="ok", text=label, **fields)]
    if current is not None and not _ours(current):
        # Never replace the operator's own status line.
        return [StatusCheck(level="warn", text=f"{label} already runs another status line; leave it unchanged", **fields)]
    if not apply:
        return [StatusCheck(level="MISSING", text=label, **fields)]
    if not dry_run:
        data["statusLine"] = {**(current if isinstance(current, dict) else {}), "type": "command", "command": command}
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fileio.atomic_write(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"), mode)
    return [StatusCheck(level="render", text=label, **fields)]


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


def read_limits(settings: dict[str, Any], *, statusline: bool = False) -> dict[str, Any]:
    """Plan limits for the enabled Usage sources that report them."""
    plans = []
    if settings.get("claude", True):
        plans.append(_claude_plan(statusline))
    if settings.get("codex", True):
        plans.append(_cached("codex", _codex_plan))
    return {"readAt": _now_ms(), "plans": plans}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _T3_CACHE.clear()
