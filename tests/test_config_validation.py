"""Fleet configuration validation and skill-name rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import config

VALID_NAMES = ("a", "skill2", "skill-name", "skill_name", "skill2-name_3")
INVALID_NAMES = (
    "",
    ".hidden",
    "two words",
    "two\nlines",
    "has:colon",
    "Uppercase",
    "nonascii-żółw",
    "-leading",
    "trailing_",
    "double--dash",
    "mixed-_separator",
    "nested/name",
    r"nested\name",
)


@pytest.mark.parametrize("name", VALID_NAMES)
def test_valid_skill_names_are_accepted(name: str) -> None:
    assert config.validate_name(name, "skill name") == name


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_skill_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="lowercase ASCII"):
        config.validate_name(name, "skill name")


def test_context_reports_the_machine_and_configuration_paths(content: Path) -> None:
    ctx = config.load_context(content)
    assert ctx["repo"] == content
    assert ctx["paths"]["agents"] == content / "config" / "agents.toml"
    assert sorted(ctx["agents"]) == ["claude"]


def test_unknown_adapter_key_is_rejected(content: Path) -> None:
    (content / "config" / "agents.toml").write_text('[claude]\nskills = "~/x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown adapter key"):
        config.load_context(content)


def test_unknown_machine_in_skills_is_rejected(content: Path) -> None:
    (content / "config" / "skills.toml").write_text(
        '[alpha]\nmachines = ["nowhere"]\n', encoding="utf-8"
    )
    with pytest.raises(config.ConfigError, match="unknown machine id"):
        config.load_context(content)


def test_machine_override_must_name_a_configured_machine(content: Path, monkeypatch) -> None:
    monkeypatch.setenv(config.MACHINE_ENV, "nowhere")
    with pytest.raises(config.ConfigError, match=config.MACHINE_ENV):
        config.load_context(content)


def test_machine_override_selects_a_configured_machine(content: Path, monkeypatch) -> None:
    monkeypatch.setenv(config.MACHINE_ENV, "other-machine")
    assert config.load_context(content)["machine_id"] == "other-machine"
