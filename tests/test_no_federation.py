"""The Console has local Usage and no peer transport or token authorization."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import webapp
from conftest import write


@pytest.mark.parametrize("method,route", [
    ("GET", "/api/peers"),
    ("POST", "/api/peers/testmachine/run"),
    ("POST", "/api/peers/remote-machine/run"),
])
def test_removed_peer_routes_return_not_found(server: str, method: str, route: str) -> None:
    request = urllib.request.Request(
        f"{server}{route}",
        data=json.dumps({"command": "apply"}).encode() if method == "POST" else None,
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"},
        method=method,
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 404


def test_retired_peer_token_cannot_authorize_mutations(
    server: str, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_HUB_PEER_TOKEN", "retired-secret")
    token_file = home / ".config" / "agent-hub" / "peer-token"
    token_file.write_text("retired-secret\n", encoding="utf-8")
    request = urllib.request.Request(
        f"{server}/api/run",
        data=json.dumps({"command": "apply"}).encode(),
        headers={"Content-Type": "application/json", "X-Hub-Token": "retired-secret"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 401
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_usage_reads_only_the_local_summary_even_with_retired_peer_configuration(
    server: str, content: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(
        content / "config" / "peers.toml",
        '[urls]\ntestmachine="http://local.invalid"\nremote="http://remote.invalid"\n',
    )
    calls = []
    expected = {"local-summary": True}

    def local_summary(*, days: int, time_zone: str | None) -> dict:
        calls.append((days, time_zone))
        return expected

    monkeypatch.setattr(webapp.usage, "read_summary", local_summary)
    with urllib.request.urlopen(f"{server}/api/usage?days=7&tz=UTC", timeout=5) as response:
        assert json.loads(response.read()) == expected
    assert calls == [(7, "UTC")]
