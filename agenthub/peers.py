"""Federate status, Usage, and commands across trusted Machines."""

from __future__ import annotations

import concurrent.futures
import hmac
import json
import os
import socket
import ssl
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import config as hub_config
from . import files as content_files
from . import fileio
from . import usage

PEER_TOKEN_ENV = "AGENT_HUB_PEER_TOKEN"
PEER_TOKEN_FILE_ENV = "AGENT_HUB_PEER_TOKEN_FILE"
DEFAULT_PEER_TOKEN_FILE = "~/.config/agent-hub/peer-token"
PEER_TIMEOUT = 5
PEER_RUN_TIMEOUT = 120
USAGE_PEER_TIMEOUT = 25
MAX_RESPONSE_BYTES = content_files.MAX_FILE_BYTES + 64 * 1024


class PeerError(Exception):
    """A federation failure safe to return through the Web adapter."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Peer:
    """The connection details passed to a remote transport adapter."""

    machine: str
    url: str
    resolve: dict[str, str]


@dataclass(frozen=True)
class _PeerConfig:
    token: str
    peers: dict[str, Peer]

    def enabled_peers(self, machine_id: str) -> dict[str, Peer]:
        return self.peers if machine_id in self.peers else {}


class PeerTransport(Protocol):
    def request(
        self,
        peer: Peer,
        route: str,
        *,
        timeout: int,
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> Any: ...


class HttpPeerTransport:
    """Send Peer requests with the standard-library HTTP client."""

    def request(
        self,
        peer: Peer,
        route: str,
        *,
        timeout: int,
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> Any:
        url = f"{peer.url}{route}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
            headers["X-Hub-Token"] = token
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname in peer.resolve:
            address = peer.resolve[parsed.hostname]
            netloc = f"{address}:{parsed.port}" if parsed.port else address
            headers["Host"] = parsed.netloc
            url = urllib.parse.urlunsplit(parsed._replace(netloc=netloc))
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        context = ssl.create_default_context() if url.startswith("https://") else None
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(MAX_RESPONSE_BYTES).decode(
                "utf-8", errors="replace"
            ).strip()
            raise PeerError(
                502, f"peer returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                reason = "timeout"
            raise PeerError(502, str(reason)) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise PeerError(502, "peer response is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PeerError(502, f"peer returned invalid JSON: {exc}") from exc


class _LocalMachine(Protocol):
    def git(self, *, fetch: bool) -> dict[str, Any]: ...

    def status(self, projection: hub_config.MachineProjection) -> dict[str, Any]: ...

    def usage(self, *, days: int, time_zone: str | None) -> dict[str, Any]: ...

    def run(
        self,
        projection: hub_config.MachineProjection,
        *,
        command: str,
        dry_run: bool,
    ) -> dict[str, Any]: ...


class PeerFederation:
    """Own federation rules behind one interface used by the Web adapter."""

    def __init__(
        self, repo: Path, local: _LocalMachine, transport: PeerTransport
    ) -> None:
        self._repo = repo
        self._local = local
        self._transport = transport

    def state(self) -> dict[str, Any]:
        projection = self._projection()
        machine_id = projection.machine_id
        config = _load_config(self._repo)
        configured = config.enabled_peers(machine_id)
        local = {
            "machine": machine_id,
            "local": True,
            "online": True,
            "url": None,
            "git": self._local.git(fetch=True),
            "status": _status_summary(self._local.status(projection)),
        }
        remote_peers = [
            peer
            for machine, peer in sorted(configured.items())
            if machine != machine_id
        ]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(remote_peers))
        ) as executor:
            remotes = list(executor.map(self._peer_state, remote_peers))
        machines = [local, *remotes]
        if any(not machine["online"] for machine in machines):
            in_sync: bool | None = None
        else:
            heads = {machine["git"]["head"]["sha"] for machine in machines}
            in_sync = len(heads) == 1 and all(
                machine["git"][key] == 0
                for machine in machines
                for key in ("dirty", "ahead", "behind")
            )
        return {"self": machine_id, "in_sync": in_sync, "machines": machines}

    def usage(
        self, *, days: int, time_zone: str | None, local_only: bool
    ) -> dict[str, Any]:
        try:
            machine_id = hub_config.load_machine_projection(self._repo).machine_id
        except hub_config.ConfigError:
            machine_id = os.uname().nodename
        local = usage.attach_machine(
            self._local.usage(days=days, time_zone=time_zone), machine_id
        )
        time_zone = local["timeZone"]
        if local_only:
            return usage.merge_summaries([local])
        config = _load_config(self._repo)
        configured = config.enabled_peers(machine_id)
        remote_peers = [
            peer
            for machine, peer in sorted(configured.items())
            if machine != machine_id
        ]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(remote_peers))
        ) as executor:
            remotes = list(
                executor.map(
                    lambda peer: self._peer_usage(peer, days, time_zone),
                    remote_peers,
                )
            )
        return usage.merge_summaries([local, *remotes])

    def run(
        self, machine: str, *, command: str, dry_run: bool
    ) -> dict[str, Any]:
        projection = self._projection()
        config = _load_config(self._repo)
        if machine == projection.machine_id:
            return self._local.run(
                projection, command=command, dry_run=dry_run
            )
        configured = config.enabled_peers(projection.machine_id)
        if machine not in configured:
            raise PeerError(404, f"unknown machine: {machine}")
        response = self._transport.request(
            configured[machine],
            "/api/run",
            timeout=PEER_RUN_TIMEOUT,
            payload={"command": command, "dry_run": dry_run},
            token=config.token,
        )
        if not isinstance(response, dict):
            raise PeerError(502, "peer returned an unexpected response")
        return response

    def authorizes(self, supplied_token: str) -> bool:
        token = _load_config(self._repo).token
        return bool(token) and hmac.compare_digest(supplied_token, token)

    def _peer_state(self, peer: Peer) -> dict[str, Any]:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                git_future = executor.submit(
                    self._transport.request,
                    peer,
                    "/api/git?fetch=1",
                    timeout=PEER_TIMEOUT,
                )
                status_future = executor.submit(
                    self._transport.request,
                    peer,
                    "/api/status",
                    timeout=PEER_TIMEOUT,
                )
                remote_git = git_future.result()
                remote_status = status_future.result()
            if not isinstance(remote_git, dict) or not isinstance(
                remote_status, dict
            ):
                raise PeerError(502, "peer returned an unexpected response")
        except PeerError as exc:
            return {
                "machine": peer.machine,
                "local": False,
                "online": False,
                "url": peer.url,
                "error": exc.message,
                "git": None,
                "status": None,
            }
        return {
            "machine": peer.machine,
            "local": False,
            "online": True,
            "url": peer.url,
            "git": remote_git,
            "status": _status_summary(remote_status),
        }

    def _peer_usage(
        self, peer: Peer, days: int, time_zone: str
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"days": days, "tz": time_zone, "local": "1"}
        )
        try:
            payload = self._transport.request(
                peer,
                f"/api/usage?{query}",
                timeout=USAGE_PEER_TIMEOUT,
            )
        except PeerError as exc:
            detail = exc.message
            if "HTTP 404" in detail:
                detail = (
                    "older agent-hub without a usage API; transcripts on this "
                    "machine are not included"
                )
            return usage.peer_failure(peer.machine, detail)
        if not isinstance(payload, dict) or "buckets" not in payload:
            return usage.peer_failure(peer.machine, "unexpected usage response")
        return usage.attach_machine(payload, peer.machine)

    def _projection(self) -> hub_config.MachineProjection:
        try:
            return hub_config.load_machine_projection(self._repo)
        except hub_config.ConfigError as exc:
            raise PeerError(500, str(exc)) from exc
        except (OSError, UnicodeError) as exc:
            raise PeerError(500, str(exc)) from exc


def _load_token() -> str:
    environment_token = os.environ.get(PEER_TOKEN_ENV, "").strip()
    if environment_token:
        return environment_token
    token_path = Path(
        os.path.expanduser(os.environ.get(PEER_TOKEN_FILE_ENV, DEFAULT_PEER_TOKEN_FILE))
    )
    try:
        return fileio.read_secret(token_path, require_value=True)
    except fileio.SecretFileError as exc:
        if exc.kind == "not_file":
            message = f"peer token path is not a regular file: {token_path}"
        elif exc.kind == "permissions":
            message = f"peer token file must have mode 600: {token_path}"
        elif exc.kind == "empty":
            message = f"peer token file is empty: {token_path}"
        else:
            message = f"cannot read peer token file {token_path}: {exc.detail}"
        raise PeerError(500, message) from exc


def _load_config(repo: Path) -> _PeerConfig:
    path = repo / "config" / "peers.toml"
    if not path.is_file():
        return _PeerConfig(token=_load_token(), peers={})
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PeerError(500, f"{path}: cannot read TOML: {exc}") from exc
    urls = data.get("urls", {})
    resolve = data.get("resolve", {})
    if "token" in data:
        raise PeerError(
            500,
            f"{path}: key 'token' is not allowed; use {PEER_TOKEN_ENV} or "
            f"{DEFAULT_PEER_TOKEN_FILE}",
        )
    if not isinstance(urls, dict):
        raise PeerError(500, f"{path}: key 'urls' must be a table")
    if not isinstance(resolve, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value.strip()
        for key, value in resolve.items()
    ):
        raise PeerError(
            500,
            f"{path}: key 'resolve' must be a table of hostname = \"ip\"",
        )
    clean_urls: dict[str, str] = {}
    for machine, url in urls.items():
        if (
            not isinstance(machine, str)
            or not isinstance(url, str)
            or not url.strip()
        ):
            raise PeerError(500, f"{path}: peer URLs must be non-empty strings")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PeerError(500, f"{path}: invalid URL for '{machine}': {url}")
        clean_urls[machine] = url.rstrip("/")
    peers = {
        machine: Peer(machine, url, dict(resolve))
        for machine, url in clean_urls.items()
    }
    return _PeerConfig(token=_load_token(), peers=peers)


def _status_summary(result: dict[str, Any]) -> dict[str, int]:
    if isinstance(result.get("problems"), int):
        problems = result["problems"]
    else:
        problem_levels = {"MISSING", "DRIFT", "STALE", "ERROR"}
        problems = sum(
            1 for line in result["lines"] if line.get("level") in problem_levels
        )
    return {"exit_code": int(result["exit_code"]), "problems": problems}
