"""Console changes to hub.toml: Skill targets and Agent settings."""

from __future__ import annotations

import json
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import config, hubconfig, operations
from conftest import HUB_TOML, write

SAME_ORIGIN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def projection(content: Path) -> config.MachineProjection:
    return config.load_machine_projection(content)


def hub(content: Path) -> dict:
    return tomllib.loads((content / "hub.toml").read_text())


def test_skill_targets_add_change_and_clear(content: Path, home: Path) -> None:
    hubconfig.set_skill_targets(projection(content), "alpha", ["testmachine"], None)
    assert hub(content)["skills"] == {"alpha": {"machines": ["testmachine"]}}
    assert (home / ".claude" / "skills" / "alpha").is_symlink()

    report = hubconfig.set_skill_targets(projection(content), "alpha", ["other"], ["claude"])
    assert report.exit_code == 0
    assert hub(content)["skills"] == {"alpha": {"machines": ["other"], "agents": ["claude"]}}
    assert not (home / ".claude" / "skills" / "alpha").exists()

    hubconfig.set_skill_targets(projection(content), "alpha", None, None)
    assert "skills" not in hub(content)
    assert (content / "hub.toml").read_text() == HUB_TOML
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_edits_keep_comments_and_other_tables(content: Path) -> None:
    text = (
        "# Store settings\n"
        '[agents]\nenabled = [\n  "claude",\n]  # only Claude\nmode = "symlink"\n\n'
        "[agents.claude]\n"
        'name = "Claude"\nuniversal = false\nskills_global = "~/.claude/skills"\n'
        'instructions_global = "~/.claude/CLAUDE.md"\n\n'
        '# keep beta on the mini\n["skills"."beta"]\n"machines" = ["mini"]\n'
    )
    write(content / "hub.toml", text)

    hubconfig.set_skill_targets(projection(content), "alpha", None, ["claude"])
    hubconfig.set_agents(projection(content), None, "copy")

    result = (content / "hub.toml").read_text()
    assert "# Store settings" in result and "# keep beta on the mini" in result
    data = tomllib.loads(result)
    assert data["agents"]["mode"] == "copy" and "enabled" not in data["agents"]
    assert data["agents"]["claude"]["name"] == "Claude"
    assert data["skills"] == {"beta": {"machines": ["mini"]}, "alpha": {"agents": ["claude"]}}


def test_agents_table_is_created_when_missing(content: Path) -> None:
    write(content / "hub.toml", '[skills.alpha]\nmachines = ["testmachine"]\n')
    hubconfig.set_agents(projection(content), ["codex"], "symlink")
    assert hub(content) == {
        "agents": {"enabled": ["codex"], "mode": "symlink"},
        "skills": {"alpha": {"machines": ["testmachine"]}},
    }


def test_unknown_agent_is_rejected_and_file_is_restored(content: Path) -> None:
    before = (content / "hub.toml").read_text()
    with pytest.raises(hubconfig.HubConfigError) as caught:
        hubconfig.set_skill_targets(projection(content), "alpha", None, ["no-such-agent"])
    assert caught.value.status == 422
    assert (content / "hub.toml").read_text() == before


def test_layout_that_cannot_be_edited_safely_is_refused(content: Path) -> None:
    # A dotted key inside [skills] is valid TOML, but not a [skills.alpha] table.
    write(content / "hub.toml", HUB_TOML + '\n[skills]\nalpha.machines = ["x"]\n')
    before = (content / "hub.toml").read_text()
    with pytest.raises(hubconfig.HubConfigError) as caught:
        hubconfig.set_skill_targets(projection(content), "alpha", ["testmachine"], None)
    assert caught.value.status == 422
    assert (content / "hub.toml").read_text() == before


@pytest.mark.parametrize(
    ("machines", "agents"),
    [([], None), (["Bad Name"], None), ("mini", None), (None, [1])],
)
def test_invalid_targets(content: Path, machines, agents) -> None:
    with pytest.raises(hubconfig.HubConfigError) as caught:
        hubconfig.set_skill_targets(projection(content), "alpha", machines, agents)
    assert caught.value.status == 400


def test_state_lists_settings_and_machines(content: Path) -> None:
    write(content / "machines" / "laptop.json", json.dumps({"machine": "laptop", "agents": ["codex"]}))
    hubconfig.set_skill_targets(projection(content), "alpha", ["laptop"], None)
    state = operations.ContentOperations(content).state()
    assert state["hub"] == {"enabled": ["claude"], "mode": "symlink", "skills": {"alpha": {"machines": ["laptop"]}}}
    assert state["machines"] == [
        {"machine": "laptop", "agents": ["codex"], "local": False},
        {"machine": "testmachine", "agents": [], "local": True},
    ]


def test_http_route(server: str, content: Path) -> None:
    def post(payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{server}/api/config", data=json.dumps(payload).encode(), headers=SAME_ORIGIN, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    status, payload = post({"action": "skill-targets", "name": "alpha", "machines": ["testmachine"], "agents": None})
    assert status == 200 and payload["command"] == "skill targets"
    status, payload = post({"action": "agents", "enabled": None, "mode": "copy"})
    assert status == 200 and hub(content)["agents"]["mode"] == "copy"
    status, _ = post({"action": "agents", "enabled": None, "mode": "hardlink"})
    assert status == 400
    status, _ = post({"action": "nope"})
    assert status == 400
