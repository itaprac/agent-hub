"""Content operations: one serialized interface for reports and files."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pytest

from agenthub import core, operations


@pytest.fixture
def content_operations(content: Path) -> operations.ContentOperations:
    return operations.ContentOperations(content)


def test_status_returns_the_structured_report(
    content_operations: operations.ContentOperations,
) -> None:
    report = content_operations.status()

    assert isinstance(report, core.StatusReport)
    assert report.command == "status"
    assert report.problems == 2


def test_machine_id_and_state_use_the_loaded_machine_projection(
    content_operations: operations.ContentOperations, content: Path
) -> None:
    state = content_operations.state()

    assert content_operations.machine_id() == state["machine_id"]
    assert state["repo"] == str(content)
    assert [skill["name"] for skill in state["skills"]["global"]] == ["alpha"]


def test_apply_returns_the_structured_report_and_deploys(
    content_operations: operations.ContentOperations, home: Path
) -> None:
    report = content_operations.apply()

    assert isinstance(report, core.ApplyReport)
    assert report.exit_code == 0
    assert (home / ".claude" / "skills" / "alpha").is_symlink()


def test_sync_dry_run_returns_the_structured_report_without_deploying(
    content_operations: operations.ContentOperations, home: Path
) -> None:
    report = content_operations.sync(dry_run=True)

    assert isinstance(report, core.SyncReport)
    assert report.command == "--dry-run sync"
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_add_skill_returns_the_structured_report_and_creates_the_template(
    content_operations: operations.ContentOperations, content: Path
) -> None:
    report = content_operations.add_skill("gamma")

    assert isinstance(report, core.AddSkillReport)
    assert report.exit_code == 0
    assert (content / "skills" / "gamma" / "SKILL.md").is_file()


def test_adopt_returns_the_structured_report_and_leaves_a_link(
    content_operations: operations.ContentOperations, content: Path, home: Path
) -> None:
    source = home / "local-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("# local-skill\n", encoding="utf-8")

    report = content_operations.adopt(str(source))

    assert isinstance(report, core.AdoptReport)
    assert report.exit_code == 0
    assert source.is_symlink()
    assert source.resolve() == (content / "skills" / "local-skill").resolve()


def test_file_operations_return_revision_checked_results(
    content_operations: operations.ContentOperations, content: Path
) -> None:
    created = content_operations.write_file(
        "agents/claude.md", "[alpha]\n", None
    )
    opened = content_operations.read_file("agents/claude.md")
    deleted = content_operations.delete_file(
        "agents/claude.md", opened["revision"]
    )

    assert opened["revision"] == created["revision"]
    assert deleted == {"path": "agents/claude.md", "deleted": True}
    assert not (content / "agents" / "claude.md").exists()


def test_invalid_configuration_is_a_structured_report_for_every_command(
    content_operations: operations.ContentOperations, content: Path, home: Path
) -> None:
    (content / "hub.toml").write_text("not = [toml\n", encoding="utf-8")

    reports = (
        content_operations.status(),
        content_operations.apply(),
        content_operations.sync(),
        content_operations.add_skill("gamma"),
        content_operations.adopt(str(home / "local-skill")),
    )

    assert [report.exit_code for report in reports] == [2, 2, 2, 2, 2]
    assert all([check.level for check in report.checks] == ["ERROR"] for report in reports)


def hold_operation(
    operation: Callable[[], object], entered: threading.Event, release: threading.Event
) -> object:
    entered.set()
    assert release.wait(timeout=5)
    return operation()


def test_report_operation_fails_immediately_during_contention(
    content_operations: operations.ContentOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    load = operations.config.load_machine_projection
    monkeypatch.setattr(
        operations.config,
        "load_machine_projection",
        lambda repo: hold_operation(lambda: load(repo), entered, release),
    )
    thread = threading.Thread(target=content_operations.status)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(operations.RepositoryBusyError):
            content_operations.status()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_file_mutation_fails_immediately_during_contention(
    content_operations: operations.ContentOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    write = operations.files.write
    monkeypatch.setattr(
        operations.files,
        "write",
        lambda *args: hold_operation(lambda: write(*args), entered, release),
    )
    thread = threading.Thread(
        target=lambda: content_operations.write_file(
            "agents/claude.md", "[alpha]\n", None
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(operations.RepositoryBusyError):
            content_operations.write_file("agents/claude.md", "", None)
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_consistent_file_read_participates_in_serialization(
    content_operations: operations.ContentOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    write = operations.files.write
    monkeypatch.setattr(
        operations.files,
        "write",
        lambda *args: hold_operation(lambda: write(*args), entered, release),
    )
    thread = threading.Thread(
        target=lambda: content_operations.write_file(
            "agents/claude.md", "[alpha]\n", None
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(operations.RepositoryBusyError):
            content_operations.read_file("hub.toml")
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_state_snapshot_participates_in_serialization(
    content_operations: operations.ContentOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    write = operations.files.write
    monkeypatch.setattr(
        operations.files,
        "write",
        lambda *args: hold_operation(lambda: write(*args), entered, release),
    )
    thread = threading.Thread(
        target=lambda: content_operations.write_file(
            "agents/claude.md", "[alpha]\n", None
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(operations.RepositoryBusyError):
            content_operations.state()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
