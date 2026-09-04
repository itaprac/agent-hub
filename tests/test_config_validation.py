"""Store configuration, Machine identity, and skill-name rules."""

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


def test_projection_reports_the_machine_and_agents(content: Path) -> None:
    projection = config.load_machine_projection(content)
    assert projection.repo == content
    assert projection.machine_id == "testmachine"
    assert [agent.name for agent in projection.agents] == ["claude"]


@pytest.mark.parametrize(
    ("document", "key"),
    [
        ('[agents]\nenabled = "claude-code"\n', "agents.enabled"),
        ('[agents]\nenabled = ["unknown-agent"]\n', "agents.enabled"),
        ('[agents]\nmode = "hardlink"\n', "agents.mode"),
        ('[agents.claude-code]\nskills = "~/x"\n', "agents.claude-code.skills"),
        ('[agents.claude-code]\nskills_global = 3\n', "agents.claude-code.skills_global"),
        ('[agents.claude-code]\nuniversal = "yes"\n', "agents.claude-code.universal"),
        ('[skills.alpha]\nagents = "claude-code"\n', "skills.alpha.agents"),
        ('[skills.alpha]\nagents = ["unknown-agent"]\n', "skills.alpha.agents"),
        ('[skills.alpha]\nmachines = [3]\n', "skills.alpha.machines"),
        ('[skills.alpha]\nhosts = ["mini"]\n', "skills.alpha.hosts"),
    ],
)
def test_invalid_config_names_the_file_and_key(
    content: Path, document: str, key: str
) -> None:
    path = content / "hub.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(config.ConfigError) as error:
        config.load_machine_projection(content)

    assert str(path) in str(error.value)
    assert key in str(error.value)


def test_invalid_toml_names_the_file(content: Path) -> None:
    path = content / "hub.toml"
    path.write_text("not = [toml\n", encoding="utf-8")
    with pytest.raises(config.ConfigError) as error:
        config.load_machine_projection(content)
    assert str(path) in str(error.value)


def test_filters_accept_machine_ids_without_a_machine_table(content: Path) -> None:
    with (content / "hub.toml").open("a", encoding="utf-8") as handle:
        handle.write('[skills.alpha]\nmachines = ["future-machine"]\n')

    projection = config.load_machine_projection(content)

    assert projection.machine_id == "testmachine"
    assert projection.skill_targets == ()


def test_empty_skill_filter_allows_every_machine_and_agent(content: Path) -> None:
    with (content / "hub.toml").open("a", encoding="utf-8") as handle:
        handle.write("[skills.alpha]\n")
    projection = config.load_machine_projection(content)
    assert [target.name for target in projection.skill_targets] == ["alpha"]


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("Work-Mac.local", "work-mac"),
        ("MINI.LAN", "mini"),
        ("My_Mac.local", "my-mac"),
        ("testmachine", "testmachine"),
    ],
)
def test_machine_id_defaults_to_sanitized_short_hostname(
    content: Path, home: Path, monkeypatch: pytest.MonkeyPatch, hostname: str, expected: str
) -> None:
    (home / ".config" / "agent-hub" / "machine").unlink()
    monkeypatch.setattr(config, "machine_name", lambda: hostname)

    projection = config.load_machine_projection(content)

    assert projection.machine_id == expected
    assert projection.hostname == hostname


def test_machine_file_keeps_identity_after_hostname_changes(
    content: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = home / ".config" / "agent-hub" / "machine"
    path.write_text("pinned-machine\n", encoding="utf-8")
    monkeypatch.setattr(config, "machine_name", lambda: "old.local")
    first = config.load_machine_projection(content)
    monkeypatch.setattr(config, "machine_name", lambda: "new.local")
    second = config.load_machine_projection(content)
    assert first.machine_id == second.machine_id == "pinned-machine"
    assert second.hostname == "new.local"


def test_legacy_machine_environment_does_not_override_the_machine_file(
    content: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_HUB_MACHINE", "old-override")
    assert config.load_machine_projection(content).machine_id == "testmachine"


def test_invalid_utf8_config_names_the_file(content: Path) -> None:
    path = content / "hub.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(config.ConfigError) as error:
        config.load_machine_projection(content)
    assert str(path) in str(error.value)


def test_generated_machine_id_can_be_pinned(
    content: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = home / ".config" / "agent-hub" / "machine"
    path.unlink()
    monkeypatch.setattr(config, "machine_name", lambda: "my__mac.local")
    machine_id = config.load_machine_projection(content).machine_id
    assert machine_id == "my--mac"

    path.write_text(f"{machine_id}\n", encoding="utf-8")
    monkeypatch.setattr(config, "machine_name", lambda: "renamed.local")

    assert config.load_machine_projection(content).machine_id == machine_id
