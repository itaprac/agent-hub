"""Disable, enable, and delete Skills and instructions; instruction backups."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import config, core, files, lifecycle, operations
from conftest import write

SAME_ORIGIN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def post(base: str, route: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{route}", data=json.dumps(payload).encode("utf-8"), headers=SAME_ORIGIN, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def projection(content: Path) -> config.MachineProjection:
    return config.load_machine_projection(content)


def test_disable_moves_skill_out_and_prunes_link(content: Path, home: Path) -> None:
    core.apply_report(projection(content))
    link = home / ".claude" / "skills" / "alpha"
    assert link.is_symlink()

    report = lifecycle.skill(projection(content), "disable", "alpha")

    assert report.exit_code == 0
    assert not (content / "skills" / "alpha").exists()
    assert (content / "disabled" / "skills" / "alpha" / "SKILL.md").is_file()
    assert not link.exists() and not link.is_symlink()
    state = operations.ContentOperations(content).state()
    assert [(skill["name"], skill["disabled"]) for skill in state["skills"]["global"]] == [("alpha", True)]
    assert state["skills"]["global"][0]["files"][0]["path"] == "disabled/skills/alpha/SKILL.md"


def test_enable_restores_skill_and_removes_empty_disabled_tree(content: Path, home: Path) -> None:
    lifecycle.skill(projection(content), "disable", "alpha")
    report = lifecycle.skill(projection(content), "enable", "alpha")

    assert report.exit_code == 0
    assert (content / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (content / "disabled").exists()
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_enable_refuses_to_overwrite_an_enabled_skill(content: Path) -> None:
    lifecycle.skill(projection(content), "disable", "alpha")
    write(content / "skills" / "alpha" / "SKILL.md", "# new alpha\n")
    with pytest.raises(lifecycle.LifecycleError) as caught:
        lifecycle.skill(projection(content), "enable", "alpha")
    assert caught.value.status == 409
    assert (content / "disabled" / "skills" / "alpha").is_dir()


def test_disable_moves_skills_sh_provenance(content: Path) -> None:
    lock = {"version": 3, "skills": {"alpha": {"source": "owner/repo"}, "other": {"source": "x/y"}}}
    write(content / ".skill-lock.json", json.dumps(lock))

    lifecycle.skill(projection(content), "disable", "alpha")
    root = json.loads((content / ".skill-lock.json").read_text())
    disabled = json.loads((content / "disabled" / ".skill-lock.json").read_text())
    assert set(root["skills"]) == {"other"}
    assert disabled == {"version": 3, "skills": {"alpha": {"source": "owner/repo"}}}
    state = operations.ContentOperations(content).state()
    assert state["skills"]["global"][0]["installed"] is True

    lifecycle.skill(projection(content), "enable", "alpha")
    assert set(json.loads((content / ".skill-lock.json").read_text())["skills"]) == {"alpha", "other"}
    assert not (content / "disabled").exists()


def test_delete_removes_enabled_or_disabled_skill(content: Path, home: Path) -> None:
    core.apply_report(projection(content))
    lifecycle.skill(projection(content), "delete", "alpha")
    assert not (content / "skills" / "alpha").exists()
    assert not (home / ".claude" / "skills" / "alpha").is_symlink()

    lifecycle.skill(projection(content), "disable", "beta", "demo")
    assert (content / "disabled" / "projects" / "demo" / "skills" / "beta").is_dir()
    lifecycle.skill(projection(content), "delete", "beta", "demo")
    assert not (content / "disabled").exists()


@pytest.mark.parametrize("name", ["../skills", ".hidden", "a/b", ""])
def test_skill_names_cannot_escape(content: Path, name: str) -> None:
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.skill(projection(content), "delete", name)
    assert (content / "skills" / "alpha").is_dir()


def test_disabled_agents_md_keeps_an_empty_managed_block(content: Path, home: Path) -> None:
    target = home / ".claude" / "CLAUDE.md"
    write(target, "Operator text\n")
    core.apply_report(projection(content))
    assert "Global base" in target.read_text()

    lifecycle.instruction(projection(content), "disable", "AGENTS.md")

    text = target.read_text()
    assert "Operator text" in text
    assert "Global base" not in text
    assert core.BEGIN_MARKER in text and core.END_MARKER in text
    entry = operations.ContentOperations(content).state()["instructions"]["global"][0]
    assert entry == {
        "name": "AGENTS.md", "path": "disabled/AGENTS.md", "source": "AGENTS.md",
        "exists": True, "disabled": True, "kind": "base",
    }

    lifecycle.instruction(projection(content), "enable", "disabled/AGENTS.md")
    assert "Global base" in target.read_text()
    assert not (content / "disabled").exists()


def test_disabled_overlay_leaves_base_instructions(content: Path, home: Path) -> None:
    write(content / "agents" / "claude.md", "Claude only\n")
    core.apply_report(projection(content))
    target = home / ".claude" / "CLAUDE.md"
    assert "Claude only" in target.read_text()

    lifecycle.instruction(projection(content), "disable", "agents/claude.md")

    assert "Claude only" not in target.read_text()
    assert "Global base" in target.read_text()
    overlays = operations.ContentOperations(content).state()["instructions"]["global"][1:]
    assert overlays[0]["path"] == "disabled/agents/claude.md" and overlays[0]["disabled"]


def test_backup_create_restore_delete(content: Path, home: Path) -> None:
    result = lifecycle.backup(projection(content), "create", "AGENTS.md", label="Before trim!")
    backup_path = content / result["backup"]
    assert backup_path.read_text() == "Global base\n"
    assert backup_path.name.endswith("--before-trim.md")

    edited = files.write(content, "AGENTS.md", "Trimmed\n", files.read(content, "AGENTS.md")["revision"])
    core.apply_report(projection(content))
    entries = lifecycle.backups(content)["AGENTS.md"]
    assert entries[0]["label"] == "before trim"

    restored = lifecycle.backup(
        projection(content), "restore", "AGENTS.md", backup_id=entries[0]["id"], revision=edited["revision"]
    )
    assert (content / "AGENTS.md").read_text() == "Global base\n"
    assert restored["apply"]["exit_code"] == 0
    assert "Global base" in (home / ".claude" / "CLAUDE.md").read_text()

    lifecycle.backup(projection(content), "delete", "AGENTS.md", backup_id=entries[0]["id"])
    assert not (content / "backups").exists()


def test_backup_restore_needs_current_revision(content: Path) -> None:
    created = lifecycle.backup(projection(content), "create", "AGENTS.md")
    backup_id = Path(created["backup"]).name
    with pytest.raises(files.FileError) as caught:
        lifecycle.backup(projection(content), "restore", "AGENTS.md", backup_id=backup_id, revision="0" * 64)
    assert caught.value.status == 409


def test_two_backups_in_one_second_do_not_collide(content: Path) -> None:
    first = lifecycle.backup(projection(content), "create", "AGENTS.md")
    second = lifecycle.backup(projection(content), "create", "AGENTS.md")
    assert first["backup"] != second["backup"]
    assert len(lifecycle.backups(content)["AGENTS.md"]) == 2


def test_backup_ids_cannot_escape(content: Path) -> None:
    lifecycle.backup(projection(content), "create", "AGENTS.md")
    for backup_id in ("../../AGENTS.md", "20260101-120000/../x.md", "nope.md"):
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.backup(projection(content), "delete", "AGENTS.md", backup_id=backup_id)


def test_disabled_and_backup_files_are_readable(content: Path) -> None:
    lifecycle.backup(projection(content), "create", "AGENTS.md")
    lifecycle.skill(projection(content), "disable", "alpha")
    assert files.read(content, "disabled/skills/alpha/SKILL.md")["content"] == "# alpha\n"
    backup_path = lifecycle.backups(content)["AGENTS.md"][0]["path"]
    assert files.read(content, backup_path)["content"] == "Global base\n"
    with pytest.raises(files.FileError):
        files.resolve(content, "disabled/hub.toml")


def test_http_routes(server: str, content: Path) -> None:
    status, payload = post(server, "/api/skill", {"action": "disable", "name": "alpha"})
    assert status == 200 and payload["command"] == "disable skill" and payload["exit_code"] == 0
    status, payload = post(server, "/api/skill", {"action": "disable", "name": "alpha"})
    assert status == 404
    status, payload = post(server, "/api/skill", {"action": "explode", "name": "alpha"})
    assert status == 400
    status, payload = post(server, "/api/instruction", {"action": "disable", "path": "AGENTS.md"})
    assert status == 200 and payload["command"] == "disable instruction"
    status, payload = post(server, "/api/instruction", {"action": "disable", "path": "hub.toml"})
    assert status == 400
    status, payload = post(server, "/api/backup", {"action": "create", "path": "disabled/AGENTS.md"})
    assert status == 200 and payload["backup"].startswith("backups/AGENTS.md/")
    status, payload = post(server, "/api/backup", {"action": "restore", "path": "disabled/AGENTS.md", "id": "x.md"})
    assert status == 428
