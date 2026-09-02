"""Peer federation behavior through its public interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from agenthub import files
from agenthub.peers import HttpPeerTransport, Peer, PeerError, PeerFederation


def git_payload(sha: str = "a" * 40) -> dict[str, Any]:
    return {
        "branch": "main",
        "head": {"sha": sha, "short": sha[:7], "subject": "fixture", "date": "2026-09-02"},
        "dirty": 0,
        "ahead": 0,
        "behind": 0,
        "remote": "origin/main",
        "fetch_error": None,
    }


class LocalMachine:
    def __init__(self, usage_result: dict[str, Any] | None = None) -> None:
        self.usage_result = usage_result
        self.runs: list[tuple[str, bool]] = []

    def git(self, *, fetch: bool) -> dict[str, Any]:
        assert fetch is True
        return git_payload()

    def status(self, projection: object) -> dict[str, Any]:
        return {"exit_code": 0, "problems": 0, "lines": []}

    def usage(self, *, days: int, time_zone: str | None) -> dict[str, Any]:
        assert self.usage_result is not None
        return self.usage_result

    def run(
        self, projection: object, *, command: str, dry_run: bool
    ) -> dict[str, Any]:
        self.runs.append((command, dry_run))
        return {"exit_code": 0, "lines": [{"level": "ok", "text": command}]}


@dataclass
class MemoryTransport:
    responses: dict[tuple[str, str], Any]
    requests: list[tuple[Peer, str, int, dict[str, Any] | None, str]] = field(
        default_factory=list
    )

    def request(
        self,
        peer: Peer,
        route: str,
        *,
        timeout: int,
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> Any:
        self.requests.append((peer, route, timeout, payload, token))
        response = self.responses[(peer.machine, route)]
        if isinstance(response, Exception):
            raise response
        return response


class HttpResponse(BytesIO):
    def __enter__(self) -> HttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def configure_peers(content: Path) -> None:
    (content / "config" / "peers.toml").write_text(
        '[urls]\ntestmachine = "http://self.test"\npeer-b = "http://peer-b.test"\n',
        encoding="utf-8",
    )


def test_state_reads_every_remote_machine_through_one_transport(
    content: Path, monkeypatch: Any
) -> None:
    configure_peers(content)
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    transport = MemoryTransport(
        {
            ("peer-b", "/api/git?fetch=1"): git_payload(),
            ("peer-b", "/api/status"): {"exit_code": 0, "problems": 0, "lines": []},
        }
    )

    result = PeerFederation(content, LocalMachine(), transport).state()

    assert result == {
        "self": "testmachine",
        "in_sync": True,
        "machines": [
            {
                "machine": "testmachine",
                "local": True,
                "online": True,
                "url": None,
                "git": git_payload(),
                "status": {"exit_code": 0, "problems": 0},
            },
            {
                "machine": "peer-b",
                "local": False,
                "online": True,
                "url": "http://peer-b.test",
                "git": git_payload(),
                "status": {"exit_code": 0, "problems": 0},
            },
        ],
    }
    assert [(request[1], request[2]) for request in transport.requests] == [
        ("/api/git?fetch=1", 5),
        ("/api/status", 5),
    ]


def test_state_marks_a_timed_out_machine_offline(
    content: Path, monkeypatch: Any
) -> None:
    configure_peers(content)
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    transport = MemoryTransport(
        {
            ("peer-b", "/api/git?fetch=1"): PeerError(502, "timeout"),
            ("peer-b", "/api/status"): {"exit_code": 0, "problems": 0},
        }
    )

    result = PeerFederation(content, LocalMachine(), transport).state()

    assert result["in_sync"] is None
    assert result["machines"][1] == {
        "machine": "peer-b",
        "local": False,
        "online": False,
        "url": "http://peer-b.test",
        "error": "timeout",
        "git": None,
        "status": None,
    }


def test_state_marks_an_unexpected_peer_response_offline(
    content: Path, monkeypatch: Any
) -> None:
    configure_peers(content)
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    transport = MemoryTransport(
        {
            ("peer-b", "/api/git?fetch=1"): [],
            ("peer-b", "/api/status"): {"exit_code": 0, "problems": 0},
        }
    )

    result = PeerFederation(content, LocalMachine(), transport).state()

    assert result["in_sync"] is None
    assert result["machines"][1]["online"] is False
    assert result["machines"][1]["error"] == "peer returned an unexpected response"


def test_usage_reports_an_older_peer_without_failing_the_local_summary(
    content: Path, monkeypatch: Any
) -> None:
    configure_peers(content)
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    local_summary = {
        "timeZone": "UTC",
        "resolution": "day",
        "buckets": [],
        "sources": [],
        "settings": {},
    }
    transport = MemoryTransport(
        {
            ("peer-b", "/api/usage?days=7&tz=UTC&local=1"): PeerError(
                502, "peer returned HTTP 404: Not Found"
            )
        }
    )

    result = PeerFederation(
        content, LocalMachine(local_summary), transport
    ).usage(days=7, time_zone="UTC", local_only=False)

    assert result["machines"] == ["testmachine", "peer-b"]
    assert result["sources"] == [
        {
            "provider": "hub",
            "path": "",
            "status": "failed",
            "scannedFiles": 0,
            "sessions": 0,
            "message": (
                "older agent-hub without a usage API; transcripts on this machine "
                "are not included"
            ),
            "machine": "peer-b",
        }
    ]
    assert transport.requests[0][2] == 25


def test_run_dispatches_a_remote_command_through_the_shared_transport(
    content: Path, monkeypatch: Any
) -> None:
    configure_peers(content)
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    expected = {"exit_code": 0, "lines": [{"level": "ok", "text": "done"}]}
    transport = MemoryTransport({("peer-b", "/api/run"): expected})
    local = LocalMachine()

    result = PeerFederation(content, local, transport).run(
        "peer-b", command="sync", dry_run=True
    )

    assert result == expected
    assert local.runs == []
    assert transport.requests == [
        (
            Peer("peer-b", "http://peer-b.test", {}),
            "/api/run",
            120,
            {"command": "sync", "dry_run": True},
            "shared-secret",
        )
    ]


def test_http_transport_connects_to_a_pinned_address_and_keeps_the_host(
    monkeypatch: Any,
) -> None:
    opened: list[tuple[Any, int, object]] = []

    def open_request(request: Any, *, timeout: int, context: object) -> HttpResponse:
        opened.append((request, timeout, context))
        return HttpResponse(b'{"exit_code": 0}')

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    peer = Peer(
        "peer-b",
        "http://peer-b.example.test:7338",
        {"peer-b.example.test": "192.0.2.20"},
    )

    result = HttpPeerTransport().request(peer, "/api/status", timeout=5)

    request, timeout, context = opened[0]
    assert result == {"exit_code": 0}
    assert request.full_url == "http://192.0.2.20:7338/api/status"
    assert request.get_header("Host") == "peer-b.example.test:7338"
    assert timeout == 5
    assert context is None


def test_http_transport_maps_a_timeout_to_a_peer_error(monkeypatch: Any) -> None:
    def time_out(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", time_out)

    with pytest.raises(PeerError, match="^timeout$") as raised:
        HttpPeerTransport().request(
            Peer("peer-b", "http://peer-b.test", {}), "/api/status", timeout=5
        )

    assert raised.value.status == 502


def test_http_transport_rejects_invalid_json(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: HttpResponse(b"not json"),
    )

    with pytest.raises(PeerError, match="^peer returned invalid JSON:") as raised:
        HttpPeerTransport().request(
            Peer("peer-b", "http://peer-b.test", {}), "/api/status", timeout=5
        )

    assert raised.value.status == 502


def test_http_transport_rejects_an_oversized_response(monkeypatch: Any) -> None:
    body = b"x" * (files.MAX_FILE_BYTES + 64 * 1024 + 1)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: HttpResponse(body),
    )

    with pytest.raises(PeerError, match="^peer response is too large$") as raised:
        HttpPeerTransport().request(
            Peer("peer-b", "http://peer-b.test", {}), "/api/status", timeout=5
        )

    assert raised.value.status == 502


def test_http_transport_maps_a_rejected_token(monkeypatch: Any) -> None:
    def reject(request: Any, **kwargs: object) -> None:
        assert request.get_header("X-hub-token") == "wrong-token"
        raise HTTPError(
            "http://peer-b.test/api/run",
            401,
            "Unauthorized",
            {},
            BytesIO(b'{"error":"authentication required"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)

    with pytest.raises(
        PeerError,
        match=r'^peer returned HTTP 401: \{"error":"authentication required"\}$',
    ) as raised:
        HttpPeerTransport().request(
            Peer("peer-b", "http://peer-b.test", {}),
            "/api/run",
            timeout=120,
            payload={"command": "sync", "dry_run": False},
            token="wrong-token",
        )

    assert raised.value.status == 502


def test_http_transport_uses_default_certificate_verification_for_https(
    monkeypatch: Any,
) -> None:
    verified_context = object()
    opened_contexts: list[object] = []
    monkeypatch.setattr(
        "ssl.create_default_context", lambda: verified_context
    )

    def open_request(
        request: Any, *, timeout: int, context: object
    ) -> HttpResponse:
        opened_contexts.append(context)
        return HttpResponse(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", open_request)

    HttpPeerTransport().request(
        Peer("peer-b", "https://peer-b.test", {}), "/api/status", timeout=5
    )

    assert opened_contexts == [verified_context]


def test_federation_authorizes_only_the_configured_peer_token(
    content: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "shared-secret")
    federation = PeerFederation(content, LocalMachine(), MemoryTransport({}))

    assert federation.authorizes("shared-secret") is True
    assert federation.authorizes("wrong-secret") is False
    assert federation.authorizes("") is False


def test_federation_rejects_a_token_stored_in_the_content_repo(
    content: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "environment-secret")
    (content / "config" / "peers.toml").write_text(
        'token = "committed-secret"\n[urls]\n', encoding="utf-8"
    )
    federation = PeerFederation(content, LocalMachine(), MemoryTransport({}))

    with pytest.raises(PeerError, match="key 'token' is not allowed") as raised:
        federation.authorizes("environment-secret")

    assert raised.value.status == 500
