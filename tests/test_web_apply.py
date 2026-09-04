"""HTTP contract: apply runs through the shared package, not a CLI subprocess."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import operations

SAME_ORIGIN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def post(base: str, route: str, payload: dict, headers: dict[str, str] = SAME_ORIGIN) -> dict:
    request = urllib.request.Request(
        f"{base}{route}", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_apply_deploys_through_the_package(server: str, content: Path, home: Path) -> None:
    payload = post(server, "/api/run", {"command": "apply"})
    assert payload["command"] == "apply"
    assert payload["exit_code"] == 0
    target = home / ".claude" / "skills" / "alpha"
    assert target.is_symlink()
    assert target.resolve() == content / "skills" / "alpha"
    assert "link" in {line["level"] for line in payload["lines"]}


def test_dry_run_reports_but_changes_nothing(server: str, home: Path) -> None:
    payload = post(server, "/api/run", {"command": "apply", "dry_run": True})
    assert payload["command"] == "--dry-run apply"
    assert payload["dry_run"] is True
    assert payload["exit_code"] == 0
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_apply_reports_the_same_lines_as_the_package(server: str, content: Path) -> None:
    expected = operations.ContentOperations(content).apply(dry_run=True).lines()
    payload = post(server, "/api/run", {"command": "apply", "dry_run": True})
    assert payload["lines"] == expected
    assert all(set(line) == {"level", "text"} for line in payload["lines"])


def test_drift_is_reported_with_exit_one(server: str, content: Path, home: Path) -> None:
    target = home / ".claude" / "skills" / "alpha"
    target.mkdir(parents=True)
    payload = post(server, "/api/run", {"command": "apply"})
    assert payload["exit_code"] == 1
    assert any(line["level"] == "DRIFT" for line in payload["lines"])
    assert target.is_dir() and not target.is_symlink()


def test_invalid_configuration_is_one_error_line_with_exit_two(
    server: str, content: Path
) -> None:
    (content / "hub.toml").write_text('[agents]\nmode = "hardlink"\n', encoding="utf-8")
    payload = post(server, "/api/run", {"command": "apply"})
    assert payload["exit_code"] == 2
    assert [line["level"] for line in payload["lines"]] == ["ERROR"]
    assert "mode" in payload["lines"][0]["text"]


def test_apply_requires_a_browser_identity(server: str, home: Path) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", {"command": "apply"}, headers={"Content-Type": "application/json"})
    assert error.value.code == 401
    assert not (home / ".claude" / "skills" / "alpha").exists()
