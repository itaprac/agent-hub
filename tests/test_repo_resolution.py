"""Content repository lookup: command option, environment override, then pointer."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import config


def pointer(home: Path, value: str) -> Path:
    path = home / ".config" / "agent-hub" / "root"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def test_pointer_path_follows_home(home: Path) -> None:
    assert config.repo_pointer_path() == home / ".config" / "agent-hub" / "root"


def test_explicit_option_wins(home: Path, content: Path, tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AGENT_HUB_REPO", str(other))
    pointer(home, str(other))
    assert config.resolve_repo(content) == content


def test_environment_wins_over_pointer(home: Path, content: Path, tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    pointer(home, str(other))
    monkeypatch.setenv("AGENT_HUB_REPO", str(content))
    assert config.resolve_repo() == content


def test_pointer_is_used_last(home: Path, content: Path) -> None:
    pointer(home, f"{content}\n")
    assert config.resolve_repo() == content


def test_pointer_expands_the_home_shortcut(home: Path, monkeypatch) -> None:
    repo = home / "content"
    repo.mkdir()
    pointer(home, "~/content\n")
    assert config.resolve_repo() == repo.resolve()


def test_blank_environment_override_is_ignored(home: Path, content: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HUB_REPO", "   ")
    pointer(home, str(content))
    assert config.resolve_repo() == content


def test_missing_explicit_directory_reports_the_option(home: Path, tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo(tmp_path / "absent")
    assert "--repo" in str(error.value)


def test_missing_environment_directory_reports_the_variable(home: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HUB_REPO", str(tmp_path / "absent"))
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    assert "AGENT_HUB_REPO" in str(error.value)


def test_missing_pointer_directory_reports_the_pointer_file(home: Path, tmp_path: Path) -> None:
    path = pointer(home, str(tmp_path / "absent"))
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    assert str(path) in str(error.value)


def test_empty_pointer_file_reports_the_pointer_file(home: Path) -> None:
    path = pointer(home, "\n\n")
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    assert str(path) in str(error.value)


def test_app_root_is_the_compatibility_default(home: Path, content: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_root", lambda: content)
    assert config.resolve_repo() == content


def test_unconfigured_lookup_explains_the_chain(home: Path, tmp_path: Path, monkeypatch) -> None:
    empty = tmp_path / "app"
    empty.mkdir()
    monkeypatch.setattr(config, "app_root", lambda: empty)
    with pytest.raises(config.ConfigError) as error:
        config.resolve_repo()
    message = str(error.value)
    assert "--repo" in message and "AGENT_HUB_REPO" in message
    assert str(config.repo_pointer_path()) in message
