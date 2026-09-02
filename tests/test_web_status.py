"""HTTP contract: the status response comes from the shared package, not from stdout."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import core


def get(base: str, route: str) -> dict:
    with urllib.request.urlopen(f"{base}{route}", timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_status_reports_the_same_lines_as_the_package(server: str, content: Path) -> None:
    payload = get(server, "/api/status")
    assert payload["command"] == "status"
    assert payload["exit_code"] == 1
    assert payload["lines"] == core.status(content).lines()


def test_status_keeps_the_line_shape_the_browser_reads(server: str) -> None:
    payload = get(server, "/api/status")
    assert all(set(line) == {"level", "text"} for line in payload["lines"])
    assert any(line["level"] == "MISSING" for line in payload["lines"])


def test_status_exposes_the_structured_result(server: str, content: Path) -> None:
    payload = get(server, "/api/status")
    assert payload["machine_id"] == core.status(content).machine_id
    assert payload["repo"] == str(content)
    assert payload["problems"] == 4
    kinds = {check["kind"] for check in payload["checks"]}
    assert {"project", "skill", "instruction", "git"} <= kinds


def test_status_turns_clean_after_apply(server: str, content: Path) -> None:
    from agenthub import config

    core.apply_projection(config.load_machine_projection(content))
    payload = get(server, "/api/status")
    assert payload["exit_code"] == 0
    assert payload["problems"] == 0


def test_state_reads_the_content_repository(server: str, content: Path) -> None:
    payload = get(server, "/api/state")
    assert payload["repo"] == str(content)
    assert [skill["name"] for skill in payload["skills"]["global"]] == ["alpha"]


def test_invalid_configuration_is_reported_as_a_status_error(server: str, content: Path) -> None:
    # The dashboard reads the problem from the status lines, so the request itself
    # must succeed; only /api/state fails when the fleet config cannot be read.
    (content / "config" / "agents.toml").write_text(
        '[claude]\nmode = "hardlink"\n', encoding="utf-8"
    )
    payload = get(server, "/api/status")
    assert payload["exit_code"] == 2
    assert payload["problems"] == 1
    assert [line["level"] for line in payload["lines"]] == ["ERROR"]
    assert "mode" in payload["lines"][0]["text"]


def test_invalid_configuration_fails_the_state_request(server: str, content: Path) -> None:
    (content / "config" / "agents.toml").write_text(
        '[claude]\nmode = "hardlink"\n', encoding="utf-8"
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{server}/api/state", timeout=10)
    assert error.value.code == 500
    assert "mode" in json.loads(error.value.read().decode("utf-8"))["error"]


def test_status_lines_never_span_more_than_one_line(server: str) -> None:
    payload = get(server, "/api/status")
    assert all("\n" not in line["text"] for line in payload["lines"])
