"""Authorize one Tailscale controller for restricted Store commands over SSH."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import shlex
import struct
import sys
import tempfile

from . import config, core, fileio


@dataclass(frozen=True)
class TrustReport(core.Report):
    @property
    def command(self) -> str:
        return "remote trust"


def _public_key(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("public key must be one line without control characters")
    fields = value.strip().split()
    if len(fields) == 1:
        fields.insert(0, "ssh-ed25519")
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("provide an ssh-ed25519 public key without SSH options")
    encoded = fields[1]
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("public key has invalid base64") from exc
    expected_prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(blob) != len(expected_prefix) + 32 or not blob.startswith(expected_prefix):
        raise ValueError("public key must contain one 32-byte Ed25519 key")
    return base64.b64encode(blob).decode("ascii"), hashlib.sha256(blob).hexdigest()


def _controller(value: str) -> str:
    address = ipaddress.ip_address(value)
    networks = (
        ipaddress.ip_network("100.64.0.0/10"),
        ipaddress.ip_network("fd7a:115c:a1e0::/48"),
    )
    if not any(
        address.version == network.version and address in network
        for network in networks
    ):
        raise ValueError("controller address must be a Tailscale IP address")
    return str(address)


def _safe_path(path: Path, home: Path) -> None:
    if not path.is_relative_to(home):
        raise ValueError(f"{path}: pairing files must stay inside HOME")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{current}: pairing paths must not contain symlinks")
        if current != path and current.exists() and not current.is_dir():
            raise ValueError(f"{current}: expected a directory")
    if path.exists() and not path.is_file():
        raise ValueError(f"{path}: expected a regular file")
    if path.exists() and path.stat().st_nlink != 1:
        raise ValueError(f"{path}: pairing files must not have hard links")


def _wrapper(repo: Path, executable: Path, home: Path) -> bytes:
    # The wrapper has no imports from the App or user-controlled module paths.
    return f'''"""Restricted agent-hub SSH command. Created by remote trust."""
import os
from pathlib import Path
import shlex
import sys

STORE = {str(repo)!r}
EXECUTABLE = {str(executable)!r}
HOME = {str(home)!r}
PATH = {os.pathsep.join([str(home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])!r}

def main():
    try:
        args = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        if len(args) not in (5, 6):
            raise ValueError()
        if args[1] != "--store" or args[4] != "--json":
            raise ValueError()
        if not Path(args[0]).is_absolute() or not Path(args[2]).is_absolute():
            raise ValueError()
        if str(Path(args[0]).resolve()) != EXECUTABLE or str(Path(args[2]).resolve()) != STORE:
            raise ValueError()
        if args[3] not in ("status", "apply", "sync"):
            raise ValueError()
        if len(args) == 6 and (args[5] != "--dry-run" or args[3] == "status"):
            raise ValueError()
    except (ValueError, OSError, RuntimeError):
        print("agent-hub: SSH command denied", file=sys.stderr)
        return 126
    command = [EXECUTABLE, "--store", STORE, args[3], "--json"] + args[5:]
    environment = {{key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_", "PYTHON", "DYLD_", "LD_", "AGENT_HUB_"))
        and key not in ("ENV", "BASH_ENV", "SSH_ORIGINAL_COMMAND")}}
    environment.update(HOME=HOME, PATH=PATH)
    os.execve(EXECUTABLE, command, environment)

if __name__ == "__main__":
    raise SystemExit(main())
'''.encode("utf-8")


def trust(
    public_key: str, controller_ip: str, repo: Path, executable: Path
) -> TrustReport:
    """Append a restricted public key, with a backup of existing SSH keys."""
    checks: list[core.StatusCheck] = []
    machine = hostname = ""
    try:
        encoded, fingerprint = _public_key(public_key)
        controller = _controller(controller_ip)
        home = Path.home().resolve()
        repo = repo.expanduser().resolve(strict=True)
        executable = executable.expanduser().resolve(strict=True)
        if (
            not repo.is_dir()
            or not executable.is_file()
            or not os.access(executable, os.X_OK)
        ):
            raise ValueError(
                "Store must be a directory and agent-hub must be executable"
            )
        machine, hostname = config.resolve_machine()
        authorized = home / ".ssh/authorized_keys"
        wrapper = (
            home / ".local/share/agent-hub" / f"remote-command-{fingerprint[:24]}.py"
        )
        for path in (authorized, wrapper):
            _safe_path(path, home)
        content = _wrapper(repo, executable, home)
        if wrapper.exists() and wrapper.read_bytes() != content:
            raise ValueError(
                "this key already has a different pairing configuration; remove its previous agent-hub authorization before pairing again"
            )
        forced = shlex.join([sys.executable, "-I", str(wrapper)])
        escaped = forced.replace("\\", "\\\\").replace('"', '\\"')
        line = f'restrict,from="{controller}",command="{escaped}" ssh-ed25519 {encoded} agent-hub:{fingerprint[:24]}'.encode(
            "utf-8"
        )
        existing = authorized.read_bytes() if authorized.exists() else b""
        # An unrestricted duplicate would defeat the intended restrictions.
        if any(
            entry != line and encoded.encode("ascii") in entry.split()
            for entry in existing.splitlines()
            if not entry.lstrip().startswith(b"#")
        ):
            raise ValueError(
                "this public key is already authorized with different options; leave existing keys unchanged and use a dedicated key"
            )
        for directory in (authorized.parent, wrapper.parent):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        if not wrapper.exists():
            fileio.atomic_write(wrapper, content, 0o600)
        else:
            wrapper.chmod(0o600)
        if line in existing.splitlines():
            authorized.chmod(0o600)
            message = f"controller {controller} is already trusted for {repo}"
        else:
            if authorized.exists():
                descriptor, backup = tempfile.mkstemp(
                    prefix="authorized_keys.agent-hub-backup-", dir=authorized.parent
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(existing)
                    handle.flush()
                    os.fsync(handle.fileno())
                checks.append(
                    core.StatusCheck(
                        kind="pairing",
                        level="ok",
                        text=f"SSH keys backed up to {backup}",
                        target=backup,
                    )
                )
            separator = b"\n" if existing and not existing.endswith(b"\n") else b""
            fileio.atomic_write(authorized, existing + separator + line + b"\n", 0o600)
            message = f"trusted {controller} for status, apply, and sync on {repo}"
        checks.append(
            core.StatusCheck(
                kind="pairing", level="ok", text=message, target=str(authorized)
            )
        )
    except (OSError, ValueError, config.ConfigError) as exc:
        checks.append(
            core.StatusCheck(
                kind="pairing", level="ERROR", text=core.one_line(str(exc))
            )
        )
    return TrustReport(
        machine_id=machine,
        hostname=hostname,
        repo=str(repo),
        checks=tuple(checks),
        exit_code=int(any(check.level == "ERROR" for check in checks)),
    )
