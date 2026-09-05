"""Use a repository-scoped GitHub deploy key for unattended Store Git access."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import tempfile
from typing import Any

from . import config, core, fileio


@dataclass(frozen=True)
class OriginAuthReport(core.Report):
    @property
    def command(self) -> str:
        return "remote origin-auth"


class OriginAuthError(RuntimeError):
    pass


def _run(
    args: list[str], *, cwd: Path | None = None, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(GH_HOST="github.com", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise OriginAuthError(
            f"Cannot complete {Path(args[0]).name}; check its installation, authentication, and network access"
        ) from exc
    if result.returncode not in allowed:
        raise OriginAuthError(
            f"{Path(args[0]).name} failed with exit {result.returncode}; no command output was logged"
        )
    return result


def _git(
    repo: Path, *args: str, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd=repo, allowed=allowed)


def _github_repo(origin: str) -> str:
    patterns = (
        r"https://github\.com/([^/]+)/([^/]+)",
        r"git@github\.com:([^/]+)/([^/]+)",
        r"ssh://git@github\.com/([^/]+)/([^/]+)",
    )
    match = next(
        (match for pattern in patterns if (match := re.fullmatch(pattern, origin))),
        None,
    )
    if match is None:
        raise OriginAuthError(
            "Origin must be a GitHub.com HTTPS or SSH repository URL without credentials or options"
        )
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner
    ) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
        raise OriginAuthError("Origin has an invalid GitHub owner or repository name")
    return f"{owner.lower()}/{name.lower()}"


def _safe_file(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise OriginAuthError(
            "Authentication files must stay in their expected directory"
        )
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise OriginAuthError("Authentication paths must not contain symlinks")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise OriginAuthError(
            "Authentication files must be regular files without hard links"
        )


def _ed25519(text: str) -> str:
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise OriginAuthError("Expected an Ed25519 public key")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise OriginAuthError("Invalid Ed25519 public key") from exc
    prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(blob) != len(prefix) + 32 or not blob.startswith(prefix):
        raise OriginAuthError("Invalid Ed25519 public key")
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def _json(result: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise OriginAuthError("GitHub returned an invalid API response") from exc


def _deploy_keys(slug: str) -> list[dict[str, Any]]:
    pages = _json(
        _run(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                f"repos/{slug}/keys?per_page=100",
                "--paginate",
                "--slurp",
            ]
        )
    )
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise OriginAuthError("GitHub returned an invalid deploy-key list")
    rows = [row for page in pages for row in page]
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("key"), str)
        or type(row.get("read_only")) is not bool
        for row in rows
    ):
        raise OriginAuthError("GitHub returned an invalid deploy-key entry")
    return rows


def _has_write_key(rows: list[dict[str, Any]], public: str) -> bool:
    for row in rows:
        if " ".join(row["key"].split()[:2]) == public:
            if row["read_only"] or row.get("enabled") is False:
                raise OriginAuthError(
                    "This dedicated deploy key is read-only or disabled; existing GitHub permissions were not changed"
                )
            return True
    return False


def _ssh_command(key: Path, known_hosts: Path) -> str:
    return shlex.join(
        [
            "ssh",
            "-F",
            "/dev/null",
            "-i",
            str(key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "HostKeyAlgorithms=ssh-ed25519",
            "-o",
            "ConnectTimeout=10",
        ]
    )


def configure(repo: Path) -> OriginAuthReport:
    checks: list[core.StatusCheck] = []
    machine = hostname = ""
    origin_changed = False

    def note(level: str, text: str) -> None:
        checks.append(core.StatusCheck(kind="origin-auth", level=level, text=text))

    try:
        repo = repo.expanduser().resolve(strict=True)
        home = Path.home().resolve()
        machine, hostname = config.resolve_machine()
        git_directory = repo / ".git"
        if not git_directory.is_dir() or git_directory.is_symlink():
            raise OriginAuthError(
                "Origin authentication requires a Store with its own .git directory"
            )
        if (
            Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
            != repo
        ):
            raise OriginAuthError("Choose the Store repository root")
        git_config = git_directory / "config"
        _safe_file(git_config, repo)
        origins = _git(
            repo, "config", "--local", "--get-all", "remote.origin.url"
        ).stdout.splitlines()
        if len(origins) != 1:
            raise OriginAuthError("Origin must have exactly one repository URL")
        push_urls = _git(
            repo,
            "config",
            "--local",
            "--includes",
            "--get-all",
            "remote.origin.pushurl",
            allowed=(0, 1),
        ).stdout.splitlines()
        if push_urls:
            raise OriginAuthError(
                "Store has an explicit origin push URL; review and remove it before configuring unattended SSH access"
            )
        slug = _github_repo(origins[0])
        ssh_origin = f"git@github.com:{slug}.git"
        suffix = hashlib.sha256(slug.encode()).hexdigest()[:20]
        key = home / ".ssh" / f"agent-hub-origin-{suffix}"
        public_path = Path(str(key) + ".pub")
        known_hosts = home / ".ssh/known_hosts"
        for path in (key, public_path, known_hosts):
            _safe_file(path, home)
        ssh_command = _ssh_command(key, known_hosts)
        existing = _git(
            repo,
            "config",
            "--local",
            "--includes",
            "--get-all",
            "core.sshCommand",
            allowed=(0, 1),
        ).stdout.splitlines()
        if existing and existing != [ssh_command]:
            raise OriginAuthError(
                "Store has a different local core.sshCommand; it was not overwritten"
            )
        marker = f"agent-hub-origin:{slug}"
        if key.exists() != public_path.exists():
            raise OriginAuthError(
                "Dedicated origin key is incomplete; existing key files were not overwritten"
            )
        public = None
        if key.exists():
            fields = public_path.read_text(encoding="utf-8").strip().split()
            if len(fields) != 3 or fields[2] != marker:
                raise OriginAuthError(
                    "Existing origin key is not owned by agent-hub; it was not overwritten"
                )
            if key.stat().st_mode & 0o077:
                raise OriginAuthError(
                    "Dedicated origin private key must have permissions 0600"
                )
            public = _ed25519(" ".join(fields))
            derived = _ed25519(
                _run(["ssh-keygen", "-y", "-P", "", "-f", str(key)]).stdout
            )
            if public != derived:
                raise OriginAuthError(
                    "Dedicated origin key pair does not match; it was not overwritten"
                )
        keys = _deploy_keys(slug)
        if public is not None:
            _has_write_key(keys, public)
        meta = _json(_run(["gh", "api", "--hostname", "github.com", "meta"]))
        if not isinstance(meta, dict) or not isinstance(meta.get("ssh_keys"), list):
            raise OriginAuthError("GitHub Meta did not return trusted SSH host keys")
        host_keys = [
            _ed25519(value)
            for value in meta["ssh_keys"]
            if isinstance(value, str) and value.startswith("ssh-ed25519 ")
        ]
        if not host_keys:
            raise OriginAuthError("GitHub Meta did not return an Ed25519 host key")
        if origins[0] != ssh_origin or existing != [ssh_command]:
            descriptor, backup = tempfile.mkstemp(
                prefix="config.agent-hub-origin-backup-", dir=git_directory
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(git_config.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            note("ok", f"Git configuration backed up to {backup}")
        key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key.parent.chmod(0o700)
        if public is None:
            _run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    marker,
                    "-f",
                    str(key),
                ]
            )
            key.chmod(0o600)
            public_path.chmod(0o600)
            public = _ed25519(public_path.read_text(encoding="utf-8"))
            note("ok", f"Created dedicated Store deploy key at {key}")
        if not _has_write_key(keys, public):
            _run(
                [
                    "gh",
                    "repo",
                    "deploy-key",
                    "add",
                    str(public_path),
                    "--allow-write",
                    "--title",
                    f"agent-hub-{machine}",
                    "--repo",
                    slug,
                ]
            )
            note(
                "ok",
                "Registered the dedicated deploy key with write access to this Store repository",
            )
            if not _has_write_key(_deploy_keys(slug), public):
                raise OriginAuthError(
                    "GitHub did not confirm write access for the dedicated deploy key"
                )
        original_hosts = known_hosts.read_bytes() if known_hosts.exists() else b""
        additions = [
            f"github.com {host_key}".encode("ascii")
            for host_key in host_keys
            if f"github.com {host_key}".encode("ascii")
            not in original_hosts.splitlines()
        ]
        if additions:
            separator = (
                b"\n" if original_hosts and not original_hosts.endswith(b"\n") else b""
            )
            fileio.atomic_write(
                known_hosts,
                original_hosts + separator + b"\n".join(additions) + b"\n",
                0o600,
            )
            note("ok", "Added GitHub's Ed25519 host key from its HTTPS Meta API")
        _git(
            repo,
            "-c",
            f"core.sshCommand={ssh_command}",
            "ls-remote",
            "--exit-code",
            ssh_origin,
            "HEAD",
        )
        if existing != [ssh_command]:
            _git(repo, "config", "--local", "core.sshCommand", ssh_command)
        if origins[0] != ssh_origin:
            _git(repo, "config", "--local", "remote.origin.url", ssh_origin)
            origin_changed = True
        note(
            "ok",
            "Store origin is ready for unattended Git over SSH with its dedicated deploy key",
        )
    except (
        OriginAuthError,
        config.ConfigError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        # Never include subprocess output, tokens, or private key data in a report.
        message = (
            str(exc)
            if isinstance(exc, (OriginAuthError, config.ConfigError))
            else "Cannot read or write origin authentication files"
        )
        note("ERROR", core.one_line(message))
        note(
            "skip",
            "Completed key and access setup steps were retained; "
            + (
                "the origin URL was changed"
                if origin_changed
                else "the origin URL was not changed"
            ),
        )
    return OriginAuthReport(
        machine,
        hostname,
        str(repo),
        tuple(checks),
        int(any(check.level == "ERROR" for check in checks)),
    )
