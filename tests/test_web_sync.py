"""HTTP contract: sync runs through the shared package, not a CLI subprocess."""

from __future__ import annotations

from pathlib import Path

from agenthub import operations

from test_web_apply import post


def test_sync_deploys_through_the_package(server: str, home: Path) -> None:
    payload = post(server, "/api/run", {"command": "sync"})
    assert payload["command"] == "sync"
    assert payload["exit_code"] == 0
    assert (home / ".claude" / "skills" / "alpha").is_symlink()
    assert payload["checks"]
    assert all("kind" in check for check in payload["checks"])


def test_dry_run_changes_nothing(server: str, home: Path) -> None:
    payload = post(server, "/api/run", {"command": "sync", "dry_run": True})
    assert payload["command"] == "--dry-run sync"
    assert payload["dry_run"] is True
    assert payload["exit_code"] == 0
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_invalid_configuration_is_one_error_line_with_exit_two(
    server: str, content: Path
) -> None:
    (content / "hub.toml").write_text(
        '[agents]\nmode = "hardlink"\n', encoding="utf-8"
    )
    payload = post(server, "/api/run", {"command": "sync"})
    assert payload["exit_code"] == 2
    assert [line["level"] for line in payload["lines"]] == ["ERROR"]


def test_sync_reports_the_same_lines_as_the_package(
    server: str, content: Path
) -> None:
    expected = operations.ContentOperations(content).sync(dry_run=True).lines()
    payload = post(server, "/api/run", {"command": "sync", "dry_run": True})
    assert payload["lines"] == expected


def test_sync_writes_a_record_visible_in_fleet(server: str, content: Path) -> None:
    import json
    import urllib.request

    payload = post(server, "/api/run", {"command": "sync"})
    assert payload["exit_code"] == 0
    with urllib.request.urlopen(f"{server}/api/fleet", timeout=5) as response:
        fleet = json.loads(response.read())
    assert fleet["machine_id"] == "testmachine"
    assert len(fleet["machines"]) == 1
    assert fleet["machines"][0]["machine"] == "testmachine"
    assert fleet["machines"][0]["current"] is True
    assert fleet["machines"][0]["local"] is True


def test_http_prefer_resolves_conflict(server: str, content: Path, tmp_path: Path) -> None:
    from test_sync import conflict_remote

    conflict_remote(content, tmp_path)
    payload = post(server, "/api/run", {"command": "sync", "prefer": "local"})
    assert payload["exit_code"] == 0, payload["lines"]
    assert (content / "conflict.txt").read_text() == "local\n"


def test_invalid_http_preference_is_rejected_before_sync(server: str, content: Path) -> None:
    import urllib.error

    import pytest

    from conftest import git

    before = git(content, "rev-parse", "HEAD").stdout
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/run", {"command": "sync", "prefer": "wrong"})
    assert error.value.code == 400
    assert git(content, "rev-parse", "HEAD").stdout == before
    assert not (content / "machines").exists()
