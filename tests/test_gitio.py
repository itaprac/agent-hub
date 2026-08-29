"""Package contract for Content Git state."""

from __future__ import annotations

from pathlib import Path

from agenthub import gitio


def test_state_reports_the_content_branch_and_head(content: Path) -> None:
    state = gitio.state(content, fetch=False)

    assert state["branch"] == "main"
    assert state["head"]["subject"] == "fixture content"
    assert state["head"]["short"] == state["head"]["sha"][:7]
    assert state["dirty"] == 0
    assert state["remote"] is None
    assert state["ahead"] is None
    assert state["behind"] is None
    assert state["fetch_error"] is None
