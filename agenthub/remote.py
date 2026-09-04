"""Run explicit Apply or Sync commands on locally configured SSH targets."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from typing import Any

from . import config

SSH_TIMEOUT = 180
MACHINE_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
USER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
COMMANDS = frozenset({"apply", "sync"})


class RemoteError(RuntimeError):
    """A safe diagnostic that does not include SSH output or configuration data."""


@dataclass(frozen=True)
class RemoteTarget:
    destination: str
    executable: str
    store: str
    identity_file: str | None = None


@dataclass(frozen=True)
class CheckedTarget:
    """The target and Machine identity verified before a local operation."""

    machine: str
    target: RemoteTarget


def _machine(value: Any) -> str:
    if not isinstance(value, str) or not MACHINE_PATTERN.fullmatch(value):
        raise RemoteError(
            "Machine ID must use lowercase letters, digits, and internal hyphens"
        )
    return value


def _absolute_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not PurePosixPath(value).is_absolute()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise RemoteError(
            f"Remote {field} must be an absolute path without control characters"
        )
    return value


def _destination(value: Any) -> str:
    if not isinstance(value, str) or value.count("@") > 1:
        raise RemoteError("Remote destination must be a hostname or user@hostname")
    parts = value.split("@")
    if len(parts) == 2 and not USER_PATTERN.fullmatch(parts[0]):
        raise RemoteError("Remote destination has an invalid SSH user")
    if not all(HOST_LABEL_PATTERN.fullmatch(label) for label in parts[-1].split(".")):
        raise RemoteError("Remote destination has an invalid hostname")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RemoteError("Remote JSON contains a duplicate key")
        result[key] = value
    return result


def load_remotes() -> dict[str, RemoteTarget]:
    """Read local configuration only. Store content cannot enable SSH access."""
    home = Path.home()
    path = home / ".config/agent-hub/remotes.json"
    if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)):
        raise RemoteError("Local remotes configuration must not use symlinks")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise RemoteError("Cannot read local remotes configuration") from exc
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise RemoteError("Local remotes configuration is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RemoteError("Local remotes configuration must be a Machine table")
    result = {}
    required = {"destination", "executable", "store"}
    for machine, fields in data.items():
        _machine(machine)
        if (
            not isinstance(fields, dict)
            or not required <= fields.keys()
            or fields.keys() - required - {"identity_file"}
        ):
            raise RemoteError(
                "Each remote requires destination, executable, and store; only identity_file is optional"
            )
        identity = (
            _absolute_path(fields["identity_file"], "identity_file")
            if "identity_file" in fields
            else None
        )
        result[machine] = RemoteTarget(
            destination=_destination(fields["destination"]),
            executable=_absolute_path(fields["executable"], "executable"),
            store=_absolute_path(fields["store"], "store"),
            identity_file=identity,
        )
    return result


def configured_machines() -> set[str]:
    """List explicit local targets without making a network request."""
    return set(load_remotes())


def _validate_report(
    stdout: str, machine: str, expected_command: str, returncode: int
) -> dict[str, Any]:
    try:
        report = json.loads(stdout, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise RemoteError("Remote CLI did not return a valid JSON report") from exc
    if not isinstance(report, dict):
        raise RemoteError("Remote CLI returned an unexpected report")
    if report.get("machine_id") != machine:
        raise RemoteError("Remote Machine ID does not match the configured target")
    if report.get("command") != expected_command:
        raise RemoteError("Remote CLI returned a report for a different command")
    code = report.get("exit_code")
    if type(code) is not int or code not in {0, 1, 2} or code != returncode:
        raise RemoteError("Remote CLI exit code does not match its report")
    if (
        not isinstance(report.get("hostname"), str)
        or not isinstance(report.get("repo"), str)
        or type(report.get("problems")) is not int
        or report["problems"] < 0
    ):
        raise RemoteError("Remote CLI returned invalid report fields")
    for field in ("lines", "checks"):
        entries = report.get(field)
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("level"), str)
            or not isinstance(entry.get("text"), str)
            or (field == "checks" and not isinstance(entry.get("kind"), str))
            for entry in entries
        ):
            raise RemoteError(f"Remote CLI returned invalid {field}")
    return report


def _invoke(
    target: RemoteTarget, machine: str, command: str, dry_run: bool = False
) -> dict[str, Any]:
    remote_args = [target.executable, "--store", target.store, command, "--json"]
    if dry_run:
        remote_args.append("--dry-run")
    args = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
    ]
    if target.identity_file is not None:
        args.extend(["-o", "IdentitiesOnly=yes", "-i", target.identity_file])
    args.extend([target.destination, shlex.join(remote_args)])
    try:
        completed = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(
            "SSH command timed out; the remote operation may still be running; no retry was made"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise RemoteError(
            "Cannot run SSH; check the local SSH client and target configuration"
        ) from exc
    if completed.returncode < 0 or completed.returncode == 255:
        raise RemoteError(
            "SSH connection failed; check reachability, authentication, and the trusted host key"
        )
    expected = f"--dry-run {command}" if dry_run else command
    return _validate_report(completed.stdout, machine, expected, completed.returncode)


def _checked_target(machine: str) -> RemoteTarget:
    machine = _machine(machine)
    try:
        local_machine, _ = config.resolve_machine()
    except config.ConfigError as exc:
        raise RemoteError("Cannot determine this Machine's local identity") from exc
    if machine == local_machine:
        raise RemoteError("Use the local operation for this Machine")
    target = load_remotes().get(machine)
    if target is None:
        raise RemoteError("Machine is not configured for remote control on this host")
    status = _invoke(target, machine, "status")
    if status["exit_code"] == 2:
        raise RemoteError(
            "Remote status reported a configuration error; no remote change was requested"
        )
    return target


def check(machine: str) -> CheckedTarget:
    """Verify remote identity and configuration before any local mutation."""
    return CheckedTarget(machine=_machine(machine), target=_checked_target(machine))


def run(
    machine: str,
    command: str,
    dry_run: bool = False,
    *,
    checked_target: CheckedTarget | None = None,
) -> dict[str, Any]:
    """Use a prior identity check, or verify it now; never retry SSH."""
    machine = _machine(machine)
    if not isinstance(command, str) or command not in COMMANDS:
        raise RemoteError("Remote commands are limited to apply and sync")
    if type(dry_run) is not bool:
        raise RemoteError("Remote dry_run must be a boolean")
    if checked_target is None:
        target = _checked_target(machine)
    elif (
        not isinstance(checked_target, CheckedTarget)
        or checked_target.machine != machine
    ):
        raise RemoteError("Checked target does not match the requested Machine")
    else:
        target = checked_target.target
    return _invoke(target, machine, command, dry_run)
