"""Store lookup uses the command option, environment, and ~/.agents."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import config

from conftest import write


def test_explicit_option_wins(home: Path, content: Path, tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AGENT_HUB_STORE", str(other))
    assert config.resolve_repo(content) == content


def test_environment_wins_over_default(home: Path, content: Path, monkeypatch) -> None:
    (home / ".agents").mkdir()
    monkeypatch.setenv("AGENT_HUB_STORE", str(content))
    assert config.resolve_repo() == content


def test_default_is_the_store_under_home(home: Path) -> None:
    store = home / ".agents"
    store.mkdir()
    assert config.resolve_repo() == store


def test_default_follows_store_symlink(home: Path, content: Path) -> None:
    (home / ".agents").symlink_to(content, target_is_directory=True)
    assert config.resolve_repo() == content


def test_explicit_path_expands_the_home_shortcut(home: Path) -> None:
    store = home / "store"
    store.mkdir()
    assert config.resolve_repo("~/store") == store


def test_environment_path_expands_variables(home: Path, monkeypatch) -> None:
    store = home / "store"
    store.mkdir()
    monkeypatch.setenv("AGENT_HUB_STORE", "$HOME/store")
    assert config.resolve_repo() == store


def test_blank_environment_override_is_ignored(home: Path, monkeypatch) -> None:
    store = home / ".agents"
    store.mkdir()
    monkeypatch.setenv("AGENT_HUB_STORE", "   ")
    assert config.resolve_repo() == store


def test_missing_explicit_directory_reports_the_option(home: Path, tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo(tmp_path / "absent")
    assert "--store" in str(error.value)


def test_missing_environment_directory_reports_the_variable(home: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HUB_STORE", str(tmp_path / "absent"))
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    assert "AGENT_HUB_STORE" in str(error.value)


def test_missing_default_store_suggests_init(home: Path) -> None:
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    assert str(home / ".agents") in str(error.value)
    assert "init" in str(error.value)


def test_legacy_pointer_and_environment_do_not_select_the_store(
    home: Path, content: Path, monkeypatch
) -> None:
    store = home / ".agents"
    store.mkdir()
    write(home / ".config" / "agent-hub" / "root", str(content))
    monkeypatch.setenv("AGENT_HUB_REPO", str(content))
    monkeypatch.setattr(config, "app_root", lambda: content)
    assert config.resolve_repo() == store
