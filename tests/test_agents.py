"""Agent table paths and overrides use only an isolated HOME."""

import importlib.util
from pathlib import Path

import pytest

from agenthub.agents import AgentError, expand_path, load_agents


def test_packaged_table_contains_all_upstream_agents(home):
    agents = load_agents()
    assert len(agents) == 77
    assert {"claude-code", "codex", "cursor", "gemini-cli", "grok", "zed", "eve"} <= agents.keys()
    assert agents["claude-code"].skills_global == home / ".claude/skills"
    assert agents["claude-code"].instructions_global == home / ".claude/CLAUDE.md"
    assert agents["codex"].universal
    assert agents["codex"].skills_global == home / ".agents/skills"
    assert agents["codex"].instructions_global == home / ".codex/AGENTS.md"
    assert not agents["cursor"].universal
    assert agents["eve"].skills_global is None
    assert agents["eve"].instructions_global is None


def test_detection_checks_any_home_path_and_is_not_cached(home):
    agent = load_agents()["kimi-code-cli"]
    assert not agent.detected
    (home / ".kimi").mkdir()
    assert agent.detected
    assert load_agents()["claude-code"].detected
    assert not load_agents()["codex"].detected


def test_environment_paths(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "custom-claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / "custom-codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "custom-config"))
    agents = load_agents()
    assert agents["claude-code"].skills_global == home / "custom-claude/skills"
    assert agents["claude-code"].detect == (home / "custom-claude",)
    assert agents["codex"].instructions_global == home / "custom-codex/AGENTS.md"
    assert agents["codex"].skills_global == home / ".agents/skills"
    assert agents["opencode"].skills_global == home / "custom-config/opencode/skills"


def test_path_expansion(home, monkeypatch):
    monkeypatch.setenv("AGENT_TEST_ROOT", str(home / "test"))
    assert expand_path("$AGENT_TEST_ROOT/skills") == home / "test/skills"
    assert expand_path("${AGENT_TEST_ROOT}/skills") == home / "test/skills"
    monkeypatch.delenv("AGENT_TEST_ROOT")
    assert expand_path("${AGENT_TEST_ROOT:-~/.test}/skills") == home / ".test/skills"
    monkeypatch.setenv("AGENT_TEST_ROOT", "")
    assert expand_path("${AGENT_TEST_ROOT:-~/.test}") == home / ".test"
    with pytest.raises(AgentError, match="AGENT_TEST_ROOT"):
        expand_path("$AGENT_TEST_ROOT/skills")


def test_override_and_custom_agent(home):
    agents = load_agents({
        "claude-code": {"name": "My Claude", "skills_global": "~/other/skills"},
        "my-agent": {"name": "My Agent", "skills_global": "~/.my/skills",
                     "skills_project": ".my/skills", "instructions_global": "~/.my/AGENTS.md"},
    })
    assert agents["claude-code"].name == "My Claude"
    assert agents["claude-code"].skills_global == home / "other/skills"
    assert agents["claude-code"].instructions_global == home / ".claude/CLAUDE.md"
    assert agents["my-agent"].detect == (home / ".my",)
    assert not agents["my-agent"].detected
    (home / ".my").mkdir()
    assert agents["my-agent"].detected
    assert load_agents()["claude-code"].name == "Claude Code"


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("name", 5), ("universal", "false"),
    ("skills_global", 5), ("skills_global", ""),
    ("skills_project", "../outside"), ("skills_project", "/outside"),
    ("skills_project", "C:\\outside"), ("skills_project", "a/../../outside"),
    ("skills_project", "."), ("detect", "~/.my"), ("detect", [1]),
    ("instructions_global", "$UNSET_AGENT_TEST_PATH/AGENTS.md"), ("unknown", True),
])
def test_bad_override_names_file_and_key(home, field, value, monkeypatch):
    monkeypatch.delenv("UNSET_AGENT_TEST_PATH", raising=False)
    with pytest.raises(AgentError) as error:
        load_agents({"my-agent": {field: value}}, source=home / ".agents/hub.toml")
    assert str(home / ".agents/hub.toml") in str(error.value)
    assert f"agents.my-agent.{field}" in str(error.value)


def test_bad_agent_ids_and_non_tables(home):
    with pytest.raises(AgentError, match="invalid Agent ID"):
        load_agents({"../outside": {}})
    with pytest.raises(AgentError, match="must be a table"):
        load_agents({"my-agent": []})


def refresh_module():
    path = Path(__file__).resolve().parents[1] / "tools/refresh_agents.py"
    spec = importlib.util.spec_from_file_location("refresh_agents", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refresh_keeps_paths_and_instruction_extensions():
    module = refresh_module()
    source = """<!-- supported-agents:start -->
| Agent | `--agent` | Project Path | Global Path |
|-------|-----------|--------------|-------------|
| Claude Code | `claude-code` | `.claude/skills/` | `~/.claude/skills/` |
| Eve | `eve` | `agent/skills/` | N/A (project-only) |
| Cline, Warp | `cline`, `warp` | `.agents/skills/` | `~/.agents/skills/` |
<!-- supported-agents:end -->"""
    previous = {"claude-code": {
        "skills_global": "${CLAUDE_CONFIG_DIR:-~/.claude}/skills",
        "instructions_global": "~/custom/CLAUDE.md",
        "detect": ["${CLAUDE_CONFIG_DIR:-~/.claude}"],
    }}
    table = module.parse_agents(source, previous)
    assert table["claude-code"]["skills_global"] == "${CLAUDE_CONFIG_DIR:-~/.claude}/skills"
    assert table["claude-code"]["instructions_global"] == "~/custom/CLAUDE.md"
    assert table["claude-code"]["detect"] == previous["claude-code"]["detect"]
    assert table["eve"]["skills_global"] is None
    assert table["eve"]["detect"] == []
    assert table["cline"]["universal"] and table["warp"]["universal"]
    with pytest.raises(ValueError, match="unsupported upstream global path"):
        module.parse_agents(source.replace("N/A (project-only)", "unknown"), {})
    with pytest.raises(ValueError, match="not found"):
        module.parse_agents("changed upstream syntax", {})
