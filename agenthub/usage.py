"""Scan coding-agent transcripts and return priced usage buckets.

Mirrors T3 Code's UsageService: read the provider CLIs' own session files
(~/.claude/projects, ~/.codex/sessions, ~/.grok/sessions), not agent-hub's
own logs. Cursor has no local token ledger, so it is fetched from the
dashboard API when a session token is configured.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import fileio

LITELLM_RATES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
RATES_TTL_S = 24 * 60 * 60
RATES_RETRY_S = 5 * 60
MTIME_SLACK_S = 36 * 60 * 60
UNPRICEABLE = {"<synthetic>", "synthetic", "opus", "sonnet", "haiku", "fable"}
# Codex review turns are not in LiteLLM; price them as the current Codex default.
# Grok Build session files name the sampler grok-4.6-build.
MODEL_ALIASES = {
    "codex-auto-review": "gpt-5.3-codex",
    "grok-4.6-build": "grok-4.6",
    "grok-build": "grok-4.6",
}
FORK_COPY_MAX_GAP_MS = 1000
# Grok stamps cost as integer ticks so sums stay exact. 1 USD = 10^10 ticks.
USD_TICKS = 10_000_000_000
CURSOR_EVENTS_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
CURSOR_PAGE_SIZE = 200
CURSOR_MAX_PAGES = 50
SOURCE_FLAGS = ("claude", "codex", "grok", "cursor")
SOURCE_DEFAULTS = {"claude": True, "codex": True, "grok": False, "cursor": False}
SNAPSHOT_TTL_S = 60
SNAPSHOT_MAX_ENTRIES = 32
FILE_CACHE_MAX_ENTRIES = 2048

_RATES: dict[str, dict[str, float]] = {}
_RATES_AT = 0.0
_RATES_STATUS = "unavailable"
_FILE_CACHE: OrderedDict[str, tuple[int, float, str, list[dict[str, Any]]]] = OrderedDict()
_SNAPSHOTS: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_SNAPSHOT_INFLIGHT: dict[tuple[Any, ...], threading.Event] = {}
_SNAPSHOT_LOCK = threading.Lock()
_FILE_CACHE_LOCK = threading.Lock()
_RATES_LOCK = threading.Lock()
_RATES_HOME = ""
_RATES_CACHE_CHECKED = False
_RATES_REFRESHING = False
_RATES_ATTEMPTED_AT = 0.0
_RATES_GENERATION = 0


def _valid_time_zone(name: str) -> str | None:
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return name


def _host_time_zone_name() -> str | None:
    configured = os.environ.get("TZ", "").removeprefix(":").strip()
    if configured and not configured.startswith("/"):
        valid = _valid_time_zone(configured)
        if valid:
            return valid

    try:
        local_zone = datetime.now().astimezone().tzinfo
    except (OSError, ValueError):
        local_zone = None
    key = getattr(local_zone, "key", None)
    if isinstance(key, str) and _valid_time_zone(key):
        return key

    try:
        resolved = str(Path("/etc/localtime").resolve(strict=True))
    except OSError:
        resolved = ""
    marker = "/zoneinfo/"
    if marker in resolved:
        valid = _valid_time_zone(resolved.split(marker, 1)[1])
        if valid:
            return valid

    try:
        configured = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        configured = ""
    return _valid_time_zone(configured) if configured else None


def _resolve_time_zone(time_zone: str | None) -> tuple[tzinfo, str]:
    requested = time_zone.strip() if isinstance(time_zone, str) else ""
    name = _valid_time_zone(requested) if requested else None
    if name is None:
        name = _host_time_zone_name()
    if name is None:
        return timezone.utc, "UTC"
    return ZoneInfo(name), name


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _parse_ts_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(stamp.timestamp() * 1000)


def _normalize_model(name: str) -> str:
    trimmed = name.strip().lower()
    slash = trimmed.rfind("/")
    return trimmed[slash + 1 :] if slash >= 0 else trimmed


def _empty_totals() -> dict[str, int]:
    return {
        "uncachedInputTokens": 0,
        "cachedInputTokens": 0,
        "cacheCreationTokens": 0,
        "outputTokens": 0,
        "reasoningTokens": 0,
    }


def _add_totals(left: dict[str, int], right: dict[str, int]) -> None:
    for key in left:
        left[key] += right[key]


def _token_sum(totals: dict[str, int]) -> int:
    return (
        totals["uncachedInputTokens"]
        + totals["cachedInputTokens"]
        + totals["cacheCreationTokens"]
        + totals["outputTokens"]
    )


def _config_dir() -> Path:
    return Path.home() / ".config" / "agent-hub"


def _settings_path() -> Path:
    return _config_dir() / "usage.json"


def _cursor_token_path() -> Path:
    return _config_dir() / "cursor-session-token"


def _read_secret_file(path: Path) -> str:
    try:
        return fileio.read_secret(path)
    except fileio.SecretFileError as exc:
        if exc.kind == "permissions":
            raise PermissionError(f"{path} must have mode 600") from exc
        if exc.kind == "read":
            raise OSError(exc.detail) from exc
        return ""


def _atomic_write_text(path: Path, value: str, mode: int = 0o600) -> None:
    fileio.atomic_write(path, value.encode("utf-8"), mode)


def _write_secret_file(path: Path, value: str) -> None:
    content = value if not value or value.endswith("\n") else f"{value}\n"
    _atomic_write_text(path, content)


def _source_enabled(settings: dict[str, Any], name: str) -> bool:
    value = settings.get(name, SOURCE_DEFAULTS[name])
    return value if isinstance(value, bool) else SOURCE_DEFAULTS[name]


def load_settings() -> dict[str, Any]:
    settings = dict(SOURCE_DEFAULTS)
    path = _settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict):
            for name in SOURCE_FLAGS:
                if isinstance(raw.get(name), bool):
                    settings[name] = raw[name]
    try:
        token = _read_secret_file(_cursor_token_path())
    except (OSError, PermissionError):
        token = ""
    settings["cursorToken"] = token
    settings["cursorTokenSet"] = bool(token)
    return settings


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    current = settings if settings is not None else load_settings()
    return {
        "claude": _source_enabled(current, "claude"),
        "codex": _source_enabled(current, "codex"),
        "grok": _source_enabled(current, "grok"),
        "cursor": _source_enabled(current, "cursor"),
        "cursorTokenSet": bool(current.get("cursorToken") or current.get("cursorTokenSet")),
    }


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    flags: dict[str, bool] = {}
    for name in SOURCE_FLAGS:
        if name not in patch:
            continue
        value = patch[name]
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        flags[name] = value

    token_changed = "cursorToken" in patch
    token: str | None = None
    if token_changed:
        raw_token = patch["cursorToken"]
        if raw_token is not None and not isinstance(raw_token, str):
            raise ValueError("cursorToken must be a string or null")
        token = raw_token.strip() if isinstance(raw_token, str) else None

    current = load_settings()
    current.update(flags)
    if token_changed:
        token_path = _cursor_token_path()
        if not token:
            try:
                token_path.unlink()
            except FileNotFoundError:
                pass
            current["cursorToken"] = ""
        else:
            _write_secret_file(token_path, token)
            current["cursorToken"] = token
    if flags:
        path = _settings_path()
        _atomic_write_text(
            path,
            json.dumps({name: current[name] for name in SOURCE_FLAGS}, indent=2) + "\n",
        )
    current["cursorTokenSet"] = bool(current.get("cursorToken"))
    return current


def _read_cached_rates(cache: Path) -> tuple[dict[str, dict[str, float]], float]:
    try:
        payload = json.loads(cache.read_text())
        table = _parse_rate_table(payload.get("document"))
        fetched_at = float(payload.get("fetchedAt") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}, 0.0
    return table, fetched_at


def _refresh_rates(home: str, cache: Path) -> None:
    global _RATES, _RATES_AT, _RATES_STATUS, _RATES_REFRESHING, _RATES_GENERATION
    now = time.time()
    try:
        with urllib.request.urlopen(LITELLM_RATES_URL, timeout=10) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        with _RATES_LOCK:
            if home == _RATES_HOME:
                if _RATES:
                    _RATES_STATUS = "cached"
                _RATES_REFRESHING = False
        return
    table = _parse_rate_table(document)
    if not table:
        with _RATES_LOCK:
            if home == _RATES_HOME:
                _RATES_REFRESHING = False
        return
    with _RATES_LOCK:
        if home != _RATES_HOME:
            return
        _RATES = table
        _RATES_AT = now
        _RATES_STATUS = "fresh"
        _RATES_REFRESHING = False
        _RATES_GENERATION += 1
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetchedAt": now, "document": document}))
    except OSError:
        pass


def _ensure_rates_refresh() -> None:
    global _RATES, _RATES_AT, _RATES_STATUS, _RATES_HOME
    global _RATES_CACHE_CHECKED, _RATES_REFRESHING, _RATES_ATTEMPTED_AT, _RATES_GENERATION
    home = str(Path.home())
    cache = Path(home) / ".cache" / "agent-hub" / "usage-model-rates.json"
    with _RATES_LOCK:
        if home != _RATES_HOME:
            _RATES = {}
            _RATES_AT = 0.0
            _RATES_STATUS = "unavailable"
            _RATES_HOME = home
            _RATES_CACHE_CHECKED = False
            _RATES_REFRESHING = False
            _RATES_ATTEMPTED_AT = 0.0
            _RATES_GENERATION += 1
        check_cache = not _RATES_CACHE_CHECKED
        _RATES_CACHE_CHECKED = True

    if check_cache:
        table, fetched_at = _read_cached_rates(cache)
        if table:
            with _RATES_LOCK:
                if home == _RATES_HOME and not _RATES:
                    _RATES = table
                    _RATES_AT = fetched_at
                    _RATES_STATUS = "cached"
                    _RATES_GENERATION += 1

    with _RATES_LOCK:
        now = time.time()
        if _RATES_AT and now - _RATES_AT < RATES_TTL_S:
            return
        if _RATES_REFRESHING:
            return
        if _RATES_ATTEMPTED_AT and now - _RATES_ATTEMPTED_AT < RATES_RETRY_S:
            return
        _RATES_REFRESHING = True
        _RATES_ATTEMPTED_AT = now
    refresh = threading.Thread(
        target=_refresh_rates,
        args=(home, cache),
        name="agent-hub-usage-rates",
        daemon=True,
    )
    try:
        refresh.start()
    except RuntimeError:
        with _RATES_LOCK:
            if home == _RATES_HOME:
                _RATES_REFRESHING = False


def _rates_state() -> tuple[str, int, int]:
    with _RATES_LOCK:
        return _RATES_STATUS, len(_RATES), _RATES_GENERATION


def _parse_rate_table(document: Any) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    ranks: dict[str, tuple[bool, bool]] = {}
    if not isinstance(document, dict):
        return table
    for name, raw in document.items():
        if not isinstance(raw, dict):
            continue
        inp = raw.get("input_cost_per_token")
        out = raw.get("output_cost_per_token")
        if not isinstance(inp, (int, float)) or not isinstance(out, (int, float)):
            continue
        read = raw.get("cache_read_input_token_cost")
        create = raw.get("cache_creation_input_token_cost")
        key = _normalize_model(str(name))
        # Reseller names collapse onto the bare key ("deepinfra/anthropic/
        # claude-opus-5" -> "claude-opus-5") and often omit cache rates, which
        # the fallback below prices at the full input rate. Prefer the bare
        # first-party entry, then any entry that states a cache-read rate.
        rank = ("/" not in str(name).strip(), isinstance(read, (int, float)))
        if key in table and ranks[key] >= rank:
            continue
        ranks[key] = rank
        table[key] = {
            "input": float(inp),
            "output": float(out),
            "cacheRead": float(read) if isinstance(read, (int, float)) else float(inp),
            "cacheCreate": float(create) if isinstance(create, (int, float)) else float(inp),
        }
    return table


def _lookup_rate_in(rates: dict[str, dict[str, float]], model: str) -> dict[str, float] | None:
    key = _normalize_model(model)
    if not key or key in UNPRICEABLE:
        return None
    if "@" in key:
        key = key.split("@", 1)[0]
    if key in rates:
        return rates[key]
    alias = MODEL_ALIASES.get(key)
    if alias and alias in rates:
        return rates[alias]
    return None


def _price(
    model: str,
    totals: dict[str, int],
    reported: float | None,
    rates: dict[str, dict[str, float]],
) -> tuple[float, float, str]:
    # Zero is "not billed", not a real API cost. The page is "if billed at full
    # API rate", so fall through to LiteLLM rather than recording $0.
    if isinstance(reported, (int, float)) and reported > 0:
        rate = _lookup_rate_in(rates, model)
        savings = 0.0
        if rate is not None:
            savings = totals["cachedInputTokens"] * max(0.0, rate["input"] - rate["cacheRead"])
        return float(reported), savings, "providerReported"
    rate = _lookup_rate_in(rates, model)
    if rate is None:
        return 0.0, 0.0, "unpriced"
    cost = (
        totals["uncachedInputTokens"] * rate["input"]
        + totals["cachedInputTokens"] * rate["cacheRead"]
        + totals["cacheCreationTokens"] * rate["cacheCreate"]
        + totals["outputTokens"] * rate["output"]
    )
    savings = totals["cachedInputTokens"] * max(0.0, rate["input"] - rate["cacheRead"])
    return cost, savings, "modelPriced"


def _parse_claude_line(line: str) -> dict[str, Any] | None:
    if '"usage"' not in line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    timestamp_ms = _parse_ts_ms(record.get("timestamp"))
    model = message.get("model")
    if timestamp_ms is None or not isinstance(model, str) or not model:
        return None
    message_id = message.get("id") if isinstance(message.get("id"), str) else None
    request_id = record.get("requestId") if isinstance(record.get("requestId"), str) else None
    dedupe = None if message_id is None and request_id is None else f"{message_id or ''}:{request_id or ''}"
    cost = record.get("costUSD")
    return {
        "provider": "claude",
        "timestampMs": timestamp_ms,
        "model": model,
        "sessionId": record.get("sessionId") if isinstance(record.get("sessionId"), str) else "",
        "totals": {
            "uncachedInputTokens": _int(usage.get("input_tokens")),
            "cachedInputTokens": _int(usage.get("cache_read_input_tokens")),
            "cacheCreationTokens": _int(usage.get("cache_creation_input_tokens")),
            "outputTokens": _int(usage.get("output_tokens")),
            "reasoningTokens": 0,
        },
        "reportedCostUsd": float(cost) if isinstance(cost, (int, float)) else None,
        "dedupeKey": dedupe,
    }


def _parse_codex_line(line: str, state: dict[str, Any]) -> dict[str, Any] | None:
    if '"token_count"' not in line and '"turn_context"' not in line and '"session_meta"' not in line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = record.get("type")
    if kind == "session_meta":
        if state["sawSessionMeta"]:
            return None
        state["sawSessionMeta"] = True
        ident = payload.get("id") or payload.get("session_id")
        if isinstance(ident, str):
            state["sessionId"] = ident
        stamp = _parse_ts_ms(record.get("timestamp"))
        source = payload.get("source")
        forked = isinstance(payload.get("forked_from_id"), str)
        if isinstance(source, dict):
            sub = source.get("subagent")
            if isinstance(sub, dict):
                spawn = sub.get("thread_spawn")
                if isinstance(spawn, dict) and isinstance(spawn.get("parent_thread_id"), str):
                    forked = True
        if stamp is not None and forked:
            state["suppressingForkCopies"] = True
            state["forkCopyAnchorMs"] = stamp
        return None
    if kind == "turn_context":
        model = payload.get("model")
        if isinstance(model, str):
            state["model"] = model
        return None
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    last = info.get("last_token_usage")
    if not isinstance(last, dict):
        return None
    timestamp_ms = _parse_ts_ms(record.get("timestamp"))
    if timestamp_ms is None or not state["model"]:
        return None
    signature = json.dumps(last, sort_keys=True)
    if signature == state["lastUsageSignature"]:
        return None
    state["lastUsageSignature"] = signature
    if state["suppressingForkCopies"]:
        if timestamp_ms - state["forkCopyAnchorMs"] < FORK_COPY_MAX_GAP_MS:
            state["forkCopyAnchorMs"] = timestamp_ms
            return None
        state["suppressingForkCopies"] = False
    input_tokens = _int(last.get("input_tokens"))
    cached = _int(last.get("cached_input_tokens"))
    cache_write = _int(last.get("cache_write_input_tokens"))
    output_tokens = _int(last.get("output_tokens"))
    totals = {
        "uncachedInputTokens": max(0, input_tokens - cached - cache_write),
        "cachedInputTokens": cached,
        "cacheCreationTokens": cache_write,
        "outputTokens": output_tokens,
        "reasoningTokens": min(output_tokens, _int(last.get("reasoning_output_tokens"))),
    }
    if _token_sum(totals) == 0:
        return None
    return {
        "provider": "codex",
        "timestampMs": timestamp_ms,
        "model": state["model"],
        "sessionId": state["sessionId"],
        "totals": totals,
        "reportedCostUsd": None,
        "dedupeKey": None,
    }


def _ticks_to_usd(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value) / USD_TICKS


def _parse_usd_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    trimmed = value.strip().replace(",", "")
    if trimmed.startswith("$"):
        trimmed = trimmed[1:]
    try:
        amount = float(trimmed)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _grok_totals(part: dict[str, Any]) -> dict[str, int]:
    # ACP turn_completed.usage.inputTokens is the full prompt (cache included).
    input_tokens = _int(part.get("inputTokens") if part.get("inputTokens") is not None else part.get("input_tokens"))
    cached = _int(
        part.get("cachedReadTokens")
        if part.get("cachedReadTokens") is not None
        else part.get("cacheReadInputTokens")
        if part.get("cacheReadInputTokens") is not None
        else part.get("cache_read_input_tokens")
    )
    created = _int(
        part.get("cacheCreationTokens")
        if part.get("cacheCreationTokens") is not None
        else part.get("cacheCreationInputTokens")
        if part.get("cacheCreationInputTokens") is not None
        else part.get("cache_creation_input_tokens")
    )
    output_tokens = _int(part.get("outputTokens") if part.get("outputTokens") is not None else part.get("output_tokens"))
    return {
        "uncachedInputTokens": max(0, input_tokens - cached - created),
        "cachedInputTokens": cached,
        "cacheCreationTokens": created,
        "outputTokens": output_tokens,
        "reasoningTokens": min(
            output_tokens,
            _int(part.get("reasoningTokens") if part.get("reasoningTokens") is not None else part.get("reasoning_tokens")),
        ),
    }


def _grok_reported_cost(part: dict[str, Any], fallback_ticks: Any) -> float | None:
    ticks = part.get("costUsdTicks")
    if ticks is None:
        ticks = part.get("cost_usd_ticks")
    cost = _ticks_to_usd(ticks)
    if cost is not None:
        return cost
    for key in ("costUSD", "costUsd", "cost_usd"):
        parsed = _parse_usd_amount(part.get(key))
        if parsed is not None:
            return parsed
    return _ticks_to_usd(fallback_ticks)


def _parse_grok_line(line: str) -> list[dict[str, Any]]:
    if "turn_completed" not in line or '"usage"' not in line:
        return []
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(record, dict):
        return []
    params = record.get("params")
    if not isinstance(params, dict):
        return []
    update = params.get("update")
    if not isinstance(update, dict) or update.get("sessionUpdate") != "turn_completed":
        return []
    usage = update.get("usage")
    if not isinstance(usage, dict):
        return []
    raw_ts = record.get("timestamp")
    if isinstance(raw_ts, (int, float)) and raw_ts > 0:
        timestamp_ms = int(raw_ts if raw_ts > 1_000_000_000_000 else raw_ts * 1000)
    else:
        timestamp_ms = _parse_ts_ms(raw_ts)
        if timestamp_ms is None:
            agent = (update.get("_meta") or {}).get("agentTimestampMs") if isinstance(update.get("_meta"), dict) else None
            if isinstance(agent, (int, float)) and agent > 0:
                timestamp_ms = int(agent)
            else:
                return []
    session_id = params.get("sessionId") if isinstance(params.get("sessionId"), str) else ""
    prompt_id = update.get("prompt_id") if isinstance(update.get("prompt_id"), str) else ""
    fallback_ticks = usage.get("costUsdTicks")
    model_usage = usage.get("modelUsage")
    parts: list[tuple[str, dict[str, Any]]] = []
    if isinstance(model_usage, dict) and model_usage:
        for name, part in model_usage.items():
            if isinstance(name, str) and isinstance(part, dict):
                parts.append((name, part))
    if not parts:
        parts.append(("", usage))
    records = []
    for model, part in parts:
        totals = _grok_totals(part)
        if _token_sum(totals) == 0:
            continue
        chosen = model or (part.get("model") if isinstance(part.get("model"), str) else "")
        if not chosen:
            continue
        records.append(
            {
                "provider": "grok",
                "timestampMs": timestamp_ms,
                "model": chosen,
                "sessionId": session_id,
                "totals": totals,
                "reportedCostUsd": _grok_reported_cost(part, fallback_ticks if len(parts) == 1 else None),
                "dedupeKey": f"grok:{prompt_id or session_id}:{chosen}:{timestamp_ms}",
            }
        )
    return records


def _parse_cursor_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    raw_ts = event.get("timestamp")
    timestamp_ms = None
    if isinstance(raw_ts, (int, float)) and raw_ts > 0:
        timestamp_ms = int(raw_ts if raw_ts > 1_000_000_000_000 else raw_ts * 1000)
    elif isinstance(raw_ts, str) and raw_ts.isdigit():
        value = int(raw_ts)
        timestamp_ms = value if value > 1_000_000_000_000 else value * 1000
    if timestamp_ms is None:
        return None
    model = event.get("model")
    if not isinstance(model, str) or not model:
        return None
    tokens = event.get("tokenUsage")
    if not isinstance(tokens, dict):
        tokens = {}
    input_tokens = _int(tokens.get("inputTokens"))
    cached = _int(tokens.get("cacheReadTokens") if tokens.get("cacheReadTokens") is not None else tokens.get("cacheReadInputTokens"))
    created = _int(tokens.get("cacheWriteTokens") if tokens.get("cacheWriteTokens") is not None else tokens.get("cacheCreationTokens"))
    output_tokens = _int(tokens.get("outputTokens"))
    totals = {
        "uncachedInputTokens": input_tokens,
        "cachedInputTokens": cached,
        "cacheCreationTokens": created,
        "outputTokens": output_tokens,
        "reasoningTokens": min(output_tokens, _int(tokens.get("reasoningTokens"))),
    }
    if _token_sum(totals) == 0:
        return None
    reported = _parse_usd_amount(event.get("usageBasedCosts"))
    if reported is None:
        cents = tokens.get("totalCents")
        if isinstance(cents, (int, float)) and cents > 0:
            reported = float(cents) / 100.0
    request_id = event.get("requestId") if isinstance(event.get("requestId"), str) else ""
    return {
        "provider": "cursor",
        "timestampMs": timestamp_ms,
        "model": model,
        "sessionId": request_id,
        "totals": totals,
        "reportedCostUsd": reported,
        "dedupeKey": f"cursor:{request_id or timestamp_ms}:{model}:{input_tokens}:{output_tokens}:{cached}",
    }


def _read_file(path: Path, provider: str) -> list[dict[str, Any]]:
    try:
        stat = path.stat()
    except OSError:
        return []
    cache_key = str(path)
    with _FILE_CACHE_LOCK:
        cached = _FILE_CACHE.get(cache_key)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime and cached[2] == provider:
            _FILE_CACHE.move_to_end(cache_key)
            return cached[3]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    state = {
        "model": "",
        "sessionId": "",
        "lastUsageSignature": None,
        "sawSessionMeta": False,
        "suppressingForkCopies": False,
        "forkCopyAnchorMs": 0,
    }
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if provider == "claude":
                    parsed = _parse_claude_line(line)
                    batch = [parsed] if parsed is not None else []
                elif provider == "grok":
                    batch = _parse_grok_line(line)
                else:
                    parsed = _parse_codex_line(line, state)
                    batch = [parsed] if parsed is not None else []
                for item in batch:
                    key = item["dedupeKey"]
                    if key:
                        if key in seen:
                            continue
                        seen.add(key)
                    records.append(item)
    except OSError:
        return []
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[cache_key] = (stat.st_size, stat.st_mtime, provider, records)
        _FILE_CACHE.move_to_end(cache_key)
        while len(_FILE_CACHE) > FILE_CACHE_MAX_ENTRIES:
            _FILE_CACHE.popitem(last=False)
    return records


def _list_jsonl(root: Path, min_mtime: float) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_mtime < min_mtime:
                    continue
            except OSError:
                continue
            files.append(path)
    return files


def _claude_dir() -> Path:
    home = Path.home()
    nested = home / ".claude" / "projects"
    return nested if nested.is_dir() else home / "projects"


def _codex_dir() -> Path:
    return Path.home() / ".codex" / "sessions"


def _grok_dir() -> Path:
    return Path.home() / ".grok" / "sessions"


def _list_named(root: Path, name: str, min_mtime: float) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if name not in filenames:
            continue
        path = Path(dirpath) / name
        try:
            if path.stat().st_mtime < min_mtime:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def _provider_dirs() -> list[tuple[str, Path, str | None]]:
    # (provider, directory, filename or None for all *.jsonl)
    return [
        ("claude", _claude_dir(), None),
        ("codex", _codex_dir(), None),
        ("grok", _grok_dir(), "updates.jsonl"),
    ]


def _fetch_cursor_events(token: str, window_start_ms: int, window_end_ms: int) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    page = 1
    while page <= CURSOR_MAX_PAGES:
        payload = json.dumps(
            {
                "page": page,
                "pageSize": CURSOR_PAGE_SIZE,
                "startDate": window_start_ms,
                "endDate": window_end_ms,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            CURSOR_EVENTS_URL,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"WorkosCursorSessionToken={token}",
                "Origin": "https://cursor.com",
                "User-Agent": "agent-hub",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                return [], "Cursor session token expired or was rejected"
            return [], f"Cursor usage API returned HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return [], f"Cursor usage API failed: {exc}"
        if not isinstance(body, dict):
            return [], "Cursor usage API returned an unexpected payload"
        events = body.get("usageEventsDisplay")
        if not isinstance(events, list):
            events = []
        for event in events:
            parsed = _parse_cursor_event(event)
            if parsed is not None:
                records.append(parsed)
        total = body.get("totalUsageEventsCount")
        if not events:
            break
        if isinstance(total, int) and page * CURSOR_PAGE_SIZE >= total:
            break
        if len(events) < CURSOR_PAGE_SIZE:
            break
        page += 1
    return records, None


def _build_summary(days: int, time_zone: str | None, settings: dict[str, Any]) -> dict[str, Any]:
    days = 1 if days == 1 else days if days in {7, 30, 90} else 30
    zone, time_zone = _resolve_time_zone(time_zone)

    now = datetime.now(zone)
    until_day = now.date()
    if days == 1:
        since_time = now - timedelta(hours=24)
        until_time = now
        since_day = since_time.date()
        resolution = "hour"
        window_start_ms = int(since_time.timestamp() * 1000)
        window_end_ms = int(until_time.timestamp() * 1000)
    else:
        since_day = until_day - timedelta(days=days - 1)
        since_time = until_time = None
        resolution = "day"
        start_local = datetime.combine(since_day, datetime.min.time(), tzinfo=zone)
        window_start_ms = int(start_local.timestamp() * 1000)
        window_end_ms = int(now.timestamp() * 1000)

    started = time.perf_counter()
    with _RATES_LOCK:
        rates = dict(_RATES)
        rates_status = _RATES_STATUS
    min_mtime = (window_start_ms / 1000) - MTIME_SLACK_S
    current_settings = settings
    enabled = {name for name in SOURCE_FLAGS if _source_enabled(current_settings, name)}

    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    sources = []
    host = os.uname().nodename
    seen_keys: set[str] = set()

    def add_record(record: dict[str, Any], sessions: set[str]) -> None:
        dedupe = record.get("dedupeKey")
        if dedupe:
            if dedupe in seen_keys:
                return
            seen_keys.add(dedupe)
        stamp = record["timestampMs"]
        if stamp < window_start_ms or stamp >= window_end_ms:
            return
        local = datetime.fromtimestamp(stamp / 1000, zone)
        day = local.date().isoformat()
        hour = local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:00:00.000Z"
        )
        key = (day, hour if resolution == "hour" else "", record["provider"], record["model"])
        cell = buckets.get(key)
        if cell is None:
            cell = {
                "day": day,
                "hourStart": hour if resolution == "hour" else None,
                "provider": record["provider"],
                "model": record["model"],
                "totals": _empty_totals(),
                "costUsd": 0.0,
                "cacheSavingsUsd": 0.0,
                "records": 0,
                "unpricedRecords": 0,
                "sessions": set(),
            }
            buckets[key] = cell
        _add_totals(cell["totals"], record["totals"])
        cost, savings, source = _price(record["model"], record["totals"], record["reportedCostUsd"], rates)
        cell["costUsd"] += cost
        cell["cacheSavingsUsd"] += savings
        cell["records"] += 1
        if source == "unpriced":
            cell["unpricedRecords"] += 1
        if record["sessionId"]:
            cell["sessions"].add(record["sessionId"])
            sessions.add(record["sessionId"])

    for provider, directory, filename in _provider_dirs():
        if provider not in enabled:
            continue
        if not directory.is_dir():
            sources.append(
                {
                    "provider": provider,
                    "path": str(directory),
                    "status": "missing",
                    "scannedFiles": 0,
                    "sessions": 0,
                    "message": "No transcript directory on this machine.",
                    "machine": host,
                }
            )
            continue
        files = _list_named(directory, filename, min_mtime) if filename else _list_jsonl(directory, min_mtime)
        sessions: set[str] = set()
        scanned = 0
        for path in files:
            records = _read_file(path, provider)
            if not records:
                continue
            scanned += 1
            for record in records:
                add_record(record, sessions)
        sources.append(
            {
                "provider": provider,
                "path": str(directory),
                "status": "ok",
                "scannedFiles": scanned,
                "sessions": len(sessions),
                "message": None,
                "machine": host,
            }
        )

    if "cursor" in enabled:
        token = current_settings.get("cursorToken") or ""
        if not token:
            sources.append(
                {
                    "provider": "cursor",
                    "path": "cursor.com/dashboard",
                    "status": "missing",
                    "scannedFiles": 0,
                    "sessions": 0,
                    "message": "Add a Cursor dashboard session token in Usage settings.",
                    "machine": host,
                }
            )
        else:
            cursor_records, error = _fetch_cursor_events(token, window_start_ms, window_end_ms)
            sessions = set()
            if error:
                sources.append(
                    {
                        "provider": "cursor",
                        "path": "cursor.com/dashboard",
                        "status": "failed",
                        "scannedFiles": 0,
                        "sessions": 0,
                        "message": error,
                        "machine": host,
                    }
                )
            else:
                for record in cursor_records:
                    add_record(record, sessions)
                sources.append(
                    {
                        "provider": "cursor",
                        "path": "cursor.com/dashboard",
                        "status": "ok",
                        "scannedFiles": len(cursor_records),
                        "sessions": len(sessions),
                        "message": None,
                        "machine": host,
                    }
                )

    out_buckets = []
    for cell in buckets.values():
        session_ids = cell.pop("sessions")
        cell["sessions"] = len(session_ids)
        cell["costUsd"] = round(cell["costUsd"], 6)
        cell["cacheSavingsUsd"] = round(cell["cacheSavingsUsd"], 6)
        out_buckets.append(cell)
    out_buckets.sort(key=lambda item: (item["day"], item.get("hourStart") or "", item["provider"], item["model"]))

    return {
        "readAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "timeZone": time_zone,
        "sinceDay": since_day.isoformat(),
        "untilDay": until_day.isoformat(),
        "sinceTime": since_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if since_time else None,
        "untilTime": until_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if until_time else None,
        "resolution": resolution,
        "days": days,
        "buckets": out_buckets,
        "sources": sources,
        "pricing": {
            "status": rates_status,
            "source": LITELLM_RATES_URL,
            "knownModels": len(rates),
        },
        "scanDurationMs": int((time.perf_counter() - started) * 1000),
        "machine": host,
        "settings": public_settings(current_settings),
    }


def read_summary(
    days: int = 30,
    time_zone: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_settings = settings if settings is not None else load_settings()
    _, time_zone = _resolve_time_zone(time_zone)
    _ensure_rates_refresh()
    _status, _known_models, rates_generation = _rates_state()
    key = (
        str(Path.home()),
        days,
        time_zone,
        rates_generation,
        *(_source_enabled(current_settings, name) for name in SOURCE_FLAGS),
        str(current_settings.get("cursorToken") or ""),
    )
    while True:
        now = time.monotonic()
        with _SNAPSHOT_LOCK:
            cached = _SNAPSHOTS.get(key)
            if cached is not None and now - cached[0] < SNAPSHOT_TTL_S:
                _SNAPSHOTS.move_to_end(key)
                return copy.deepcopy(cached[1])
            if cached is not None:
                del _SNAPSHOTS[key]
            pending = _SNAPSHOT_INFLIGHT.get(key)
            if pending is None:
                pending = threading.Event()
                _SNAPSHOT_INFLIGHT[key] = pending
                break
        pending.wait()

    try:
        summary = _build_summary(days, time_zone, current_settings)
    except BaseException:
        with _SNAPSHOT_LOCK:
            _SNAPSHOT_INFLIGHT.pop(key, None)
            pending.set()
        raise
    with _SNAPSHOT_LOCK:
        _SNAPSHOTS[key] = (time.monotonic(), summary)
        _SNAPSHOTS.move_to_end(key)
        while len(_SNAPSHOTS) > SNAPSHOT_MAX_ENTRIES:
            _SNAPSHOTS.popitem(last=False)
        _SNAPSHOT_INFLIGHT.pop(key, None)
        pending.set()
    return copy.deepcopy(summary)


def attach_machine(summary: dict[str, Any], machine: str) -> dict[str, Any]:
    summary["machine"] = machine
    for source in summary.get("sources") or []:
        source["machine"] = machine
    return summary


def peer_failure(machine: str, message: str) -> dict[str, Any]:
    return {
        "machine": machine,
        "buckets": [],
        "sources": [
            {
                "provider": "hub",
                "path": "",
                "status": "failed",
                "scannedFiles": 0,
                "sessions": 0,
                "message": message,
                "machine": machine,
            }
        ],
        "pricing": {"status": "unavailable", "source": "", "knownModels": 0},
        "scanDurationMs": 0,
    }


def _empty_rollup() -> dict[str, int | float]:
    return {
        **_empty_totals(),
        "totalTokens": 0,
        "costUsd": 0.0,
        "cacheSavingsUsd": 0.0,
        "records": 0,
        "unpricedRecords": 0,
        "sessions": 0,
    }


def _add_bucket_to_rollup(target: dict[str, Any], bucket: dict[str, Any]) -> None:
    totals = bucket.get("totals") or {}
    for key in _empty_totals():
        target[key] += int(totals.get(key) or 0)
    target["costUsd"] += float(bucket.get("costUsd") or 0)
    target["cacheSavingsUsd"] += float(bucket.get("cacheSavingsUsd") or 0)
    target["records"] += int(bucket.get("records") or 0)
    target["unpricedRecords"] += int(bucket.get("unpricedRecords") or 0)
    target["sessions"] += int(bucket.get("sessions") or 0)


def _finish_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    rollup["totalTokens"] = _token_sum(rollup)
    rollup["costUsd"] = round(rollup["costUsd"], 6)
    rollup["cacheSavingsUsd"] = round(rollup["cacheSavingsUsd"], 6)
    return rollup


def _rollup_rows(
    buckets: list[dict[str, Any]],
    key_for: Callable[[dict[str, Any]], Any],
    identity_for: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: dict[Any, dict[str, Any]] = {}
    for bucket in buckets:
        key = key_for(bucket)
        if key is None:
            continue
        row = rows.get(key)
        if row is None:
            row = {**identity_for(bucket), **_empty_rollup()}
            rows[key] = row
        _add_bucket_to_rollup(row, bucket)
    return [_finish_rollup(row) for row in rows.values()]


def _period_rollups(
    buckets: list[dict[str, Any]],
    resolution: str,
    sources: list[str],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for bucket in buckets:
        key = bucket.get("hourStart") if resolution == "hour" else bucket.get("day")
        if not key:
            continue
        row = rows.get(key)
        if row is None:
            row = {
                "key": key,
                "day": bucket.get("day"),
                "hourStart": bucket.get("hourStart"),
                "bySource": {name: _empty_rollup() for name in sources},
                **_empty_rollup(),
            }
            rows[key] = row
        _add_bucket_to_rollup(row, bucket)
        source_name = bucket.get("provider")
        source = row["bySource"].setdefault(source_name, _empty_rollup())
        _add_bucket_to_rollup(source, bucket)
    for row in rows.values():
        _finish_rollup(row)
        for source in row["bySource"].values():
            _finish_rollup(source)
    return list(rows.values())


def _dimension_rollups(
    keys: list[str],
    identity_field: str,
    keyed_buckets: list[tuple[str, dict[str, Any]]],
    keyed_sessions: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    rows = {key: {identity_field: key, **_empty_rollup()} for key in keys}
    for key, bucket in keyed_buckets:
        _add_bucket_to_rollup(
            rows.setdefault(key, {identity_field: key, **_empty_rollup()}),
            bucket,
        )
    sessions: dict[str, int] = defaultdict(int)
    for key, count in keyed_sessions:
        sessions[key] += count
    for key, count in sessions.items():
        if key in rows:
            rows[key]["sessions"] = count
    return [_finish_rollup(row) for row in rows.values()]


def _build_rollups(
    buckets: list[dict[str, Any]],
    machine_buckets: list[tuple[str, dict[str, Any]]],
    sources: list[dict[str, Any]],
    resolution: str,
    machines: list[str],
) -> dict[str, Any]:
    present = {
        str(item.get("provider"))
        for item in [*buckets, *sources]
        if item.get("provider") and item.get("provider") != "hub"
    }
    source_names = [name for name in SOURCE_FLAGS if name in present]
    source_names.extend(sorted(present - set(source_names)))

    total = _empty_rollup()
    for bucket in buckets:
        _add_bucket_to_rollup(total, bucket)
    total["sessions"] = sum(int(source.get("sessions") or 0) for source in sources)
    _finish_rollup(total)

    daily = _rollup_rows(
        buckets,
        lambda bucket: bucket.get("day"),
        lambda bucket: {"day": bucket.get("day")},
    )

    def week_start(bucket: dict[str, Any]) -> str | None:
        try:
            day = datetime.fromisoformat(str(bucket.get("day"))).date()
        except ValueError:
            return None
        return (day - timedelta(days=day.weekday())).isoformat()

    weekly = _rollup_rows(
        buckets,
        week_start,
        lambda bucket: {"weekStart": week_start(bucket)},
    )
    by_model = _rollup_rows(
        buckets,
        lambda bucket: (bucket.get("provider"), bucket.get("model")),
        lambda bucket: {
            "source": bucket.get("provider"),
            "model": bucket.get("model"),
        },
    )
    for row in by_model:
        row["costShare"] = row["costUsd"] / total["costUsd"] if total["costUsd"] else 0
    by_model.sort(key=lambda row: row["costUsd"], reverse=True)

    by_machine = _dimension_rollups(
        machines,
        "machine",
        machine_buckets,
        [
            (str(source["machine"]), int(source.get("sessions") or 0))
            for source in sources
            if source.get("machine")
        ],
    )
    by_source = _dimension_rollups(
        source_names,
        "source",
        [
            (str(bucket["provider"]), bucket)
            for bucket in buckets
            if bucket.get("provider")
        ],
        [
            (str(source["provider"]), int(source.get("sessions") or 0))
            for source in sources
            if source.get("provider") and source.get("provider") != "hub"
        ],
    )
    for row in by_source:
        row["costShare"] = row["costUsd"] / total["costUsd"] if total["costUsd"] else 0
        row["tokenShare"] = row["totalTokens"] / total["totalTokens"] if total["totalTokens"] else 0

    return {
        "total": total,
        "periods": _period_rollups(buckets, resolution, source_names),
        "daily": daily,
        "weekly": weekly,
        "byModel": by_model,
        "byMachine": by_machine,
        "bySource": by_source,
    }


def merge_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-machine summaries the way T3 merges environments."""
    if not parts:
        return peer_failure("local", "no usage summaries")
    primary = parts[0]
    cursor_owner = None
    for part in parts:
        for source in part.get("sources") or []:
            if source.get("provider") == "cursor" and source.get("status") == "ok":
                cursor_owner = part.get("machine")
                break
        if cursor_owner is not None:
            break
    cells: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    machine_buckets: list[tuple[str, dict[str, Any]]] = []
    duration = 0
    for part in parts:
        duration = max(duration, int(part.get("scanDurationMs") or 0))
        skip_cursor = cursor_owner is not None and part.get("machine") != cursor_owner
        for source in part.get("sources") or []:
            if skip_cursor and source.get("provider") == "cursor":
                continue
            sources.append(source)
        for bucket in part.get("buckets") or []:
            if skip_cursor and bucket.get("provider") == "cursor":
                continue
            machine = part.get("machine")
            if machine:
                machine_buckets.append((machine, bucket))
            key = (
                bucket.get("day") or "",
                bucket.get("hourStart") or "",
                bucket.get("provider") or "",
                bucket.get("model") or "",
            )
            cell = cells.get(key)
            if cell is None:
                cell = {
                    "day": bucket.get("day"),
                    "hourStart": bucket.get("hourStart"),
                    "provider": bucket.get("provider"),
                    "model": bucket.get("model"),
                    "totals": _empty_totals(),
                    "costUsd": 0.0,
                    "cacheSavingsUsd": 0.0,
                    "records": 0,
                    "unpricedRecords": 0,
                    "sessions": 0,
                }
                cells[key] = cell
            _add_totals(cell["totals"], bucket.get("totals") or _empty_totals())
            cell["costUsd"] += float(bucket.get("costUsd") or 0)
            cell["cacheSavingsUsd"] += float(bucket.get("cacheSavingsUsd") or 0)
            cell["records"] += int(bucket.get("records") or 0)
            cell["unpricedRecords"] += int(bucket.get("unpricedRecords") or 0)
            cell["sessions"] += int(bucket.get("sessions") or 0)
    out_buckets = []
    for cell in cells.values():
        cell["costUsd"] = round(cell["costUsd"], 6)
        cell["cacheSavingsUsd"] = round(cell["cacheSavingsUsd"], 6)
        out_buckets.append(cell)
    out_buckets.sort(key=lambda item: (item["day"] or "", item.get("hourStart") or "", item["provider"], item["model"]))
    merged = dict(primary)
    merged["buckets"] = out_buckets
    merged["sources"] = sources
    merged["scanDurationMs"] = duration
    merged["machines"] = [part.get("machine") for part in parts if part.get("machine")]
    merged["rollups"] = _build_rollups(
        out_buckets,
        machine_buckets,
        sources,
        str(primary.get("resolution") or "day"),
        merged["machines"],
    )
    if isinstance(primary.get("settings"), dict):
        merged["settings"] = public_settings(primary["settings"])
    return merged
