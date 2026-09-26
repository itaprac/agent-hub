"""Combine local Usage with bounded reads from explicitly configured Machines."""

from __future__ import annotations

import copy
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import config, remote, usage

_CACHE: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any] | str]] = OrderedDict()
_LOCK = threading.Lock()


def _validate(summary: dict[str, Any], local: dict[str, Any]) -> None:
    for key in ("days", "timeZone", "sinceDay", "untilDay", "resolution"):
        if summary.get(key) != local.get(key):
            raise remote.RemoteError("Remote Usage has a different date range or time zone")
    for key in ("buckets", "sources"):
        if not isinstance(summary.get(key), list):
            raise remote.RemoteError("Remote Usage has an invalid report")
    for bucket in summary["buckets"]:
        if not isinstance(bucket, dict) or bucket.get("provider") not in usage.SOURCE_FLAGS:
            raise remote.RemoteError("Remote Usage has an invalid source")
        if not isinstance(bucket.get("model"), str) or not isinstance(bucket.get("day"), str):
            raise remote.RemoteError("Remote Usage has an invalid bucket")
        if not local["sinceDay"] <= bucket["day"] <= local["untilDay"]:
            raise remote.RemoteError("Remote Usage has a bucket outside the date range")
        if local["resolution"] == "hour" and not isinstance(bucket.get("hourStart"), str):
            raise remote.RemoteError("Remote Usage has an invalid hour")
        totals = bucket.get("totals")
        if not isinstance(totals, dict):
            raise remote.RemoteError("Remote Usage has invalid token counts")
        for fields, names in ((totals, usage._empty_totals()), (bucket, (
            "costUsd", "cacheSavingsUsd", "records", "unpricedRecords", "sessions",
        ))):
            for name in names:
                value = fields.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise remote.RemoteError("Remote Usage has invalid counts or costs")
    for source in summary["sources"]:
        if (not isinstance(source, dict) or source.get("provider") not in usage.SOURCE_FLAGS
                or type(source.get("sessions")) is not int or source["sessions"] < 0):
            raise remote.RemoteError("Remote Usage has an invalid source report")


def _read(machine: str, target: remote.RemoteTarget, local: dict[str, Any]) -> dict[str, Any] | str:
    key = (machine, target, local["days"], local["timeZone"], local["sinceDay"], local["untilDay"])
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < usage.SNAPSHOT_TTL_S:
            _CACHE.move_to_end(key)
            return copy.deepcopy(cached[1])
    result: dict[str, Any] | str
    try:
        result = remote.read_usage(machine, target, local["days"], local["timeZone"])
        _validate(result, local)
    except remote.RemoteError as exc:
        result = str(exc)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > usage.SNAPSHOT_MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return copy.deepcopy(result)


def read_summary(days: int = 30, time_zone: str | None = None) -> dict[str, Any]:
    settings = usage.load_settings()
    local = usage.read_summary(days, time_zone, settings)
    local_id, _ = config.resolve_machine()
    merged = copy.deepcopy(local)
    merged["scope"] = "configuredMachines"
    merged["machines"] = [{"id": local_id, "status": "ok", "readAt": local["readAt"]}]
    merged["buckets"] = [{**b, "machine": local_id} for b in local["buckets"]]
    merged["sources"] = [{**s, "machine": local_id} for s in local["sources"]]
    by_machine = [{"machine": local_id, **local["rollups"]["total"]}]
    try:
        targets = {name: target for name, target in remote.load_remotes().items() if name != local_id}
    except remote.RemoteError as exc:
        targets = {}
        merged["machines"].append({"id": "Remote configuration", "status": "failed", "message": str(exc)})
    enabled = {name for name in usage.SOURCE_FLAGS if usage._source_enabled(settings, name)} - {"cursor"}
    if targets:
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futures = {name: pool.submit(_read, name, target, local) for name, target in targets.items()}
            for name, future in futures.items():
                result = future.result()
                if isinstance(result, str):
                    merged["machines"].append({"id": name, "status": "failed", "message": result})
                    continue
                buckets = [{**b, "machine": name} for b in result["buckets"] if b["provider"] in enabled]
                sources = [{**s, "machine": name} for s in result["sources"] if s["provider"] in enabled]
                merged["buckets"].extend(buckets)
                merged["sources"].extend(sources)
                merged["machines"].append({
                    "id": name, "status": "ok", "readAt": result.get("readAt"),
                    "pricing": result.get("pricing"),
                })
                by_machine.append({"machine": name, **usage._build_rollups(buckets, sources, local["resolution"])["total"]})
    merged["partial"] = any(m["status"] != "ok" for m in merged["machines"])
    merged["rollups"] = usage._build_rollups(merged["buckets"], merged["sources"], local["resolution"])
    merged["rollups"]["byMachine"] = by_machine
    return merged
