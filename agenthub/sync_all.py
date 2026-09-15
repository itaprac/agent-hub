"""Synchronize configured Machines through their shared Origin."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import config, core, fleet, gitio, remote


def result_path(repo: Path) -> Path:
    value = gitio.run_git(repo, "rev-parse", "--absolute-git-dir")
    if value.returncode:
        raise gitio.GitCommandError(value.stderr.strip())
    return Path(value.stdout.strip()) / "agent-hub-sync.json"


def last_result(repo: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(result_path(repo).read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, gitio.GitCommandError):
        return None


def complete(result: dict[str, Any]) -> bool:
    # A local Apply can succeed even when the Origin did not accept the push.
    return result.get("exit_code") == 0 and not any(
        line.get("level") in {"warn", "ERROR", "CONFLICT", "DRIFT", "MISSING", "STALE"}
        or (line.get("level") == "skip" and "disabled" in line.get("text", ""))
        for line in result.get("lines", [])
    )


def run(repo: Path) -> dict[str, Any]:
    import fcntl

    path = result_path(repo)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"command": "sync-all", "exit_code": 1, "lines": [
                {"level": "ERROR", "text": "Sync is already running. Wait for it to finish."}]}
        return _run(repo, path)


def _run(repo: Path, path: Path) -> dict[str, Any]:
    local, _ = config.resolve_machine()
    targets = sorted(remote.configured_machines() - {local})
    outcomes: dict[str, dict[str, Any]] = {}
    lines: list[dict[str, str]] = []

    def record(machine: str, result: dict[str, Any]) -> bool:
        ok = complete(result)
        detail = next((line["text"] for line in result.get("lines", [])
                       if line.get("level") in {"warn", "ERROR", "CONFLICT", "DRIFT", "MISSING", "STALE"}
                       or (line.get("level") == "skip" and "disabled" in line.get("text", ""))), "")
        outcomes[machine] = {"state": "synced" if ok else "pending", "detail": detail}
        lines.extend({"level": line.get("level", ""), "text": f"[{machine}] {line['text']}"}
                     for line in result.get("lines", []))
        return ok

    def sync_local() -> bool:
        return record(local, core.sync_report(config.load_machine_projection(repo)).to_dict())

    local_ok = sync_local()
    if local_ok:
        for machine in targets:
            try:
                record(machine, remote.run(machine, "sync"))
            except remote.RemoteError as exc:
                record(machine, {"exit_code": 1, "lines": [{"level": "ERROR", "text": str(exc)}]})
        if targets:
            local_ok = sync_local()
        # A later Machine can publish edits after an earlier Machine synced.
        # Reconcile those earlier Machines once, then verify; never loop on errors.
        if local_ok:
            behind = {row["machine"] for row in fleet.records(repo, local) if not row["current"]}
            retried = False
            for machine in targets:
                if machine in behind and outcomes[machine]["state"] == "synced":
                    try:
                        record(machine, remote.run(machine, "sync"))
                    except remote.RemoteError as exc:
                        record(machine, {"exit_code": 1, "lines": [{"level": "ERROR", "text": str(exc)}]})
                    retried = True
            if retried:
                sync_local()
    else:
        for machine in targets:
            outcomes[machine] = {"state": "pending", "detail": "Waiting for this Machine to publish changes."}

    records = fleet.records(repo, local)
    for row in records:
        machine = row["machine"]
        if machine not in outcomes:
            outcomes[machine] = {"state": "pending", "detail": "Remote control is not configured."}
        elif outcomes[machine]["state"] == "synced" and (not row["current"] or row["problems"]):
            outcomes[machine] = {"state": "pending", "detail": "Machine still needs sync. See details."}
    # Require a record for each configured target, including first-time pairings.
    recorded = {row["machine"] for row in records}
    for machine in outcomes:
        if machine not in recorded and outcomes[machine]["state"] == "synced":
            outcomes[machine] = {"state": "pending", "detail": "Sync has not been confirmed by a Machine record."}
    ok = bool(outcomes) and all(value["state"] == "synced" for value in outcomes.values())
    result = {"command": "sync-all", "exit_code": 0 if ok else 1,
              "lines": lines, "machines": outcomes, "at": fleet.utc_now().isoformat(timespec="seconds")}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result))
    os.replace(temporary, path)
    return result
