"""Fleet Sync must publish locally even when a target is asleep."""
from dataclasses import replace

import pytest

from agenthub import config, core, operations, remote, sync_all
from conftest import git
from test_web_apply import post


@pytest.fixture
def shared(content, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    git(content, "remote", "add", "origin", str(origin))
    git(content, "push", "-u", "origin", "main")
    target = tmp_path / "macbook"
    git(tmp_path, "clone", "-b", "main", str(origin), str(target))
    git(target, "config", "user.name", "tests")
    git(target, "config", "user.email", "tests@example.invalid")
    monkeypatch.setattr(remote, "configured_machines", lambda: {"macbook"})

    target_home = tmp_path / "macbook-home"
    (target_home / ".claude").mkdir(parents=True)

    def run(machine, command):
        assert machine == "macbook" and command == "sync"
        with monkeypatch.context() as context:
            context.setenv("HOME", str(target_home))
            context.setenv("AGENT_HUB_MACHINE", "macbook")
            projection = replace(config.load_machine_projection(target), machine_id="macbook")
            return core.sync_report(projection).to_dict()

    monkeypatch.setattr(remote, "run", run)
    return target, run


def test_one_click_applies_both_machines_and_persists_result(server, content, shared):
    target, _ = shared
    (content / "skills/alpha/SKILL.md").write_text("New skill content\n")
    result = post(server, "/api/run", {"command": "sync-all"})
    assert result["exit_code"] == 0, result
    assert set(result["machines"]) == {"testmachine", "macbook"}
    assert all(row["state"] == "synced" for row in result["machines"].values())
    assert (target / "skills/alpha/SKILL.md").read_text() == "New skill content\n"
    assert sync_all.last_result(content) == result


def test_sleeping_target_does_not_block_local_publish_and_next_run_recovers(content, shared, monkeypatch):
    target, run = shared
    (content / "skills/alpha/SKILL.md").write_text("Offline edit\n")

    def sleeping(*args):
        raise remote.RemoteError("SSH connection timed out")

    monkeypatch.setattr(remote, "run", sleeping)
    first = operations.ContentOperations(content).sync_all()
    assert first["exit_code"] == 1
    assert first["machines"]["testmachine"]["state"] == "synced"
    assert first["machines"]["macbook"]["state"] == "pending"
    assert git(content, "rev-list", "--count", "@{upstream}..HEAD").stdout.strip() == "0"
    monkeypatch.setattr(remote, "run", run)
    second = operations.ContentOperations(content).sync_all()
    assert second["exit_code"] == 0, second
    assert (target / "skills/alpha/SKILL.md").read_text() == "Offline edit\n"


def test_conflict_is_preserved_without_choosing_a_side(content, shared):
    target, _ = shared
    (content / "skills/alpha/SKILL.md").write_text("Mini edit\n")
    (target / "skills/alpha/SKILL.md").write_text("MacBook edit\n")
    result = operations.ContentOperations(content).sync_all()
    assert result["exit_code"] == 1
    assert result["machines"]["macbook"]["state"] == "pending"
    assert (target / "skills/alpha/SKILL.md").read_text() == "MacBook edit\n"
    assert (content / "skills/alpha/SKILL.md").read_text() == "Mini edit\n"
    assert any(line["level"] == "CONFLICT" for line in result["lines"])


def test_pending_push_is_not_success_even_with_exit_zero(content, shared, monkeypatch):
    monkeypatch.setattr(core, "sync_report", lambda p: core.SyncReport(
        p.machine_id, p.hostname, str(p.repo),
        (core.StatusCheck(kind="git", level="warn", text="origin unreachable; push pending"),), 0))
    monkeypatch.setattr(remote, "run", lambda *args: pytest.fail("Do not sync a target before publishing"))
    result = operations.ContentOperations(content).sync_all()
    assert result["exit_code"] == 1
    assert result["machines"]["testmachine"]["state"] == "pending"


def test_changes_from_target_are_applied_back_on_controller(content, shared):
    target, _ = shared
    (target / "skills/alpha/SKILL.md").write_text("MacBook edit\n")
    result = operations.ContentOperations(content).sync_all()
    assert result["exit_code"] == 0, result
    assert (content / "skills/alpha/SKILL.md").read_text() == "MacBook edit\n"


def test_uncommitted_edits_are_visible_in_fleet(content):
    (content / "skills/alpha/SKILL.md").write_text("Unsynced edit\n")
    assert operations.ContentOperations(content).fleet()["git"]["dirty"] > 0
