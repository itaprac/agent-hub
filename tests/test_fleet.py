"""Machine record freshness and heartbeat limits use a fixed clock and local Git."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from agenthub import config, core, fleet
from conftest import MACHINE_ID, git, write


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(fleet, "utc_now", lambda: now[0])
    return now


def sync(content):
    return core.sync_report(config.load_machine_projection(content))


def head(content):
    return git(content, "rev-parse", "HEAD").stdout.strip()


def record(content):
    return json.loads((content / "machines" / f"{MACHINE_ID}.json").read_text())


def commit(content, message):
    git(content, "add", "-A")
    git(content, "commit", "-m", message)


def test_ten_unchanged_syncs_write_one_record(content, clock):
    applied_head = head(content)
    for _ in range(10):
        report = sync(content)
        assert report.exit_code == 0, report.lines()
        clock[0] += timedelta(minutes=10)
    entries = git(content, "log", "--format=%H", "--", "machines").stdout.splitlines()
    assert len(entries) == 1
    assert record(content)["head"] == applied_head
    row = fleet.records(content, MACHINE_ID)[0]
    assert row["current"] is True and row["behind"] == 0
    assert row["local"] is True
    assert row["problems"] == 0
    assert row["age_seconds"] == 6000
    assert row["agents"] == ["claude"]


def test_record_heartbeat_after_24_hours_keeps_applied_content_head(content, clock):
    assert sync(content).exit_code == 0
    previous = record(content)
    initial_head = head(content)
    clock[0] += timedelta(hours=24)
    assert sync(content).exit_code == 0
    assert head(content) == initial_head
    clock[0] += timedelta(seconds=1)
    assert sync(content).exit_code == 0
    assert head(content) != initial_head
    assert record(content)["head"] == previous["head"]
    assert record(content)["synced_at"] == clock[0].isoformat(timespec="seconds")


def test_other_machine_heartbeat_does_not_trigger_local_record(content, clock):
    assert sync(content).exit_code == 0
    before = (content / "machines" / f"{MACHINE_ID}.json").read_bytes()
    other = {**record(content), "machine": "other", "hostname": "other"}
    write(content / "machines/other.json", json.dumps(other))
    commit(content, "machine: other")
    previous_head = head(content)
    clock[0] += timedelta(minutes=10)
    assert sync(content).exit_code == 0
    assert head(content) == previous_head
    assert (content / "machines" / f"{MACHINE_ID}.json").read_bytes() == before
    rows = fleet.records(content, MACHINE_ID)
    assert len(rows) == 2
    assert all(row["current"] for row in rows)
    assert [row["machine"] for row in rows if row["local"]] == [MACHINE_ID]


def test_content_commits_change_head_and_make_old_machine_behind(content, clock):
    assert sync(content).exit_code == 0
    old = {**record(content), "machine": "other", "hostname": "other"}
    write(content / "machines/other.json", json.dumps(old))
    commit(content, "machine: other")
    write(content / "skills/new/SKILL.md", "new content\n")
    commit(content, "add content")
    write(content / "AGENTS.md", "changed content\n")
    commit(content, "change instructions")
    applied_head = head(content)
    rows = {row["machine"]: row for row in fleet.records(content, MACHINE_ID)}
    assert rows["other"]["behind"] == 2
    assert rows["other"]["current"] is False
    assert sync(content).exit_code == 0
    assert record(content)["head"] == applied_head
    rows = {row["machine"]: row for row in fleet.records(content, MACHINE_ID)}
    assert rows[MACHINE_ID]["current"] is True
    assert rows["other"]["behind"] == 2


def test_record_status_tracks_apply_failure_then_recovery(content, home, clock):
    write(home / ".claude/skills/alpha/SKILL.md", "operator copy\n")
    report = sync(content)
    assert report.exit_code == 1
    assert record(content)["status"]["exit_code"] == 1
    assert record(content)["status"]["problems"] >= 1
    target = home / ".claude/skills/alpha"
    (target / "SKILL.md").unlink()
    target.rmdir()
    assert sync(content).exit_code == 0
    assert record(content)["status"] == {"exit_code": 0, "problems": 0}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        42,
        "text",
        {},
        {"machine": MACHINE_ID, "status": None},
        {"machine": MACHINE_ID, "status": {"problems": "bad"}},
        {"machine": MACHINE_ID, "status": {"problems": True}},
        {"machine": MACHINE_ID, "status": {"problems": -1}},
        {"machine": MACHINE_ID, "status": {"problems": 0}, "synced_at": 42},
        {
            "machine": MACHINE_ID,
            "status": {"problems": 0},
            "synced_at": "2026-09-04T12:00:00",
        },
        {"machine": "wrong-id", "problems": "bad"},
    ],
)
def test_malformed_records_are_error_rows(content, clock, payload):
    write(content / "machines" / f"{MACHINE_ID}.json", json.dumps(payload))
    row = fleet.records(content, MACHINE_ID)[0]
    assert "error" in row
    assert row["current"] is False and row["behind"] is None
    assert row["problems"] >= 1
    assert row["local"] is True
    assert "age_seconds" in row


def test_unknown_applied_commit_is_reported_without_crashing(content, clock):
    assert sync(content).exit_code == 0
    value = record(content)
    value["head"] = "0" * 40
    write(content / "machines" / f"{MACHINE_ID}.json", json.dumps(value))
    row = fleet.records(content, MACHINE_ID)[0]
    assert "not available locally" in row["error"]
    assert row["behind"] is None


def test_record_cannot_override_computed_fields(content, clock):
    assert sync(content).exit_code == 0
    value = {
        **record(content),
        "current": "bad",
        "behind": "bad",
        "local": False,
        "age_seconds": "bad",
        "problems": "bad",
        "error": "fake",
    }
    write(content / "machines" / f"{MACHINE_ID}.json", json.dumps(value))
    row = fleet.records(content, MACHINE_ID)[0]
    assert row["current"] is True and row["behind"] == 0
    assert row["local"] is True and row["problems"] == 0
    assert row["age_seconds"] == 0
    assert "error" not in row


@pytest.mark.parametrize("symlink_directory", [True, False])
def test_machine_record_write_rejects_symlinks(content, home, clock, symlink_directory):
    outside = home / "outside"
    outside.mkdir()
    destination = outside / f"{MACHINE_ID}.json"
    destination.write_text("do not change\n")
    machines = content / "machines"
    if symlink_directory:
        machines.symlink_to(outside, target_is_directory=True)
    else:
        machines.mkdir()
        (machines / f"{MACHINE_ID}.json").symlink_to(destination)
    with pytest.raises(ValueError, match="symlink|regular"):
        fleet.write_record(
            content,
            machine_id=MACHINE_ID,
            hostname="test",
            agents=[],
            head=head(content),
            exit_code=0,
            problems=0,
        )
    assert destination.read_text() == "do not change\n"


def test_dry_run_does_not_write_machine_record(content, clock):
    before = head(content)
    report = core.sync_report(config.load_machine_projection(content), dry_run=True)
    assert report.exit_code == 0
    assert not (content / "machines").exists()
    assert head(content) == before
