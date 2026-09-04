"""Initialize Stores on an isolated HOME and local Git remotes."""

import json
import os
import subprocess

import pytest

from agenthub.store import init_store
from conftest import git, write


@pytest.fixture(autouse=True)
def identity(home, monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Store tests")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "store-tests@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))


def assert_ok(report):
    assert report.exit_code == 0, report.lines()
    assert report.command == "init"


def remote_store(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    write(seed / "skills/remote/SKILL.md", "remote content\n")
    write(
        seed / ".skill-lock.json",
        json.dumps({"version": 3, "skills": {"remote": {"source": "remote/source"}}}),
    )
    git(seed, "init", "-b", "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-u", "origin", "HEAD")
    return origin


def test_init_preserves_existing_41_skills_and_provenance(home):
    store = home / ".agents"
    for index in range(41):
        write(store / f"skills/skill-{index}/SKILL.md", f"Skill {index}\n")
    lock = '{"version": 3, "skills": {"skill-0": {"source": "owner/repo"}}}\n'
    write(store / ".skill-lock.json", lock)
    write(store / "notes.txt", "operator notes\n")
    report = init_store(store, yes=True)
    assert_ok(report)
    assert len(list((store / "skills").iterdir())) == 41
    assert (store / ".skill-lock.json").read_text() == lock
    assert (store / "notes.txt").read_text() == "operator notes\n"
    assert git(store, "log", "-1", "--format=%s").stdout.strip() == "init: testmachine"
    assert git(store, "status", "--porcelain").stdout == ""
    assert ".skill-lock.json" in git(store, "ls-files").stdout


def test_alternate_store_merges_canonical_and_links_it(home, tmp_path):
    canonical = home / ".agents"
    store = tmp_path / "store"
    write(canonical / "skills/local/SKILL.md", "local\n")
    write(
        canonical / ".skill-lock.json", '{"skills":{"local":{"source":"local/repo"}}}'
    )
    write(store / "skills/other/SKILL.md", "other\n")
    assert_ok(init_store(store, yes=True))
    assert canonical.is_symlink() and canonical.resolve() == store
    assert (store / "skills/local/SKILL.md").read_text() == "local\n"
    assert (store / "skills/other/SKILL.md").read_text() == "other\n"
    assert (
        json.loads((store / ".skill-lock.json").read_text())["skills"]["local"][
            "source"
        ]
        == "local/repo"
    )


def test_alternate_store_collision_does_not_partly_move_skills(home, tmp_path):
    canonical, store = home / ".agents", tmp_path / "store"
    write(canonical / "skills/first/SKILL.md", "first\n")
    write(canonical / "skills/clash/SKILL.md", "local\n")
    write(store / "skills/clash/SKILL.md", "other\n")
    report = init_store(store, yes=True)
    assert report.exit_code == 1
    assert "name clash" in str(report.lines())
    assert not canonical.is_symlink()
    assert (canonical / "skills/first/SKILL.md").read_text() == "first\n"
    assert (canonical / "skills/clash/SKILL.md").read_text() == "local\n"
    assert (store / "skills/clash/SKILL.md").read_text() == "other\n"
    assert not (store / "skills/first").exists()
    assert not (store / ".git").exists()


def test_unrelated_canonical_symlink_is_not_replaced(home, tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (home / ".agents").symlink_to(existing, target_is_directory=True)
    requested = tmp_path / "requested"
    report = init_store(requested, yes=True)
    assert report.exit_code == 1
    assert (home / ".agents").resolve() == existing
    assert not requested.exists()


def test_clone_merges_local_skills_extra_files_and_lockfile(home, tmp_path):
    origin = remote_store(tmp_path)
    store = home / ".agents"
    write(store / "skills/local/SKILL.md", "local\n")
    write(store / "local-note.md", "keep me\n")
    write(
        store / ".skill-lock.json",
        '{"version":3,"skills":{"local":{"source":"local/repo"}}}',
    )
    assert_ok(init_store(store, from_url=str(origin), yes=True))
    assert (store / "skills/local/SKILL.md").read_text() == "local\n"
    assert (store / "skills/remote/SKILL.md").read_text() == "remote content\n"
    assert (store / "local-note.md").read_text() == "keep me\n"
    assert set(json.loads((store / ".skill-lock.json").read_text())["skills"]) == {
        "local",
        "remote",
    }
    assert git(store, "remote", "get-url", "origin").stdout.strip() == str(origin)


def test_clone_collision_preserves_all_local_content(home, tmp_path):
    origin = remote_store(tmp_path)
    store = home / ".agents"
    write(store / "skills/local/SKILL.md", "local\n")
    write(store / "skills/remote/SKILL.md", "different\n")
    report = init_store(store, from_url=str(origin), yes=True)
    assert report.exit_code == 1
    assert "skills/remote" in str(report.lines())
    assert (store / "skills/local/SKILL.md").read_text() == "local\n"
    assert (store / "skills/remote/SKILL.md").read_text() == "different\n"
    assert not (store / ".git").exists()


def test_yes_adopts_real_skills_and_skips_links_and_instruction_move(home):
    store = home / ".agents"
    skill = home / ".claude/skills/real"
    write(skill / "SKILL.md", "adopt me\n")
    write(home / ".claude/CLAUDE.md", "operator instructions\n")
    foreign = home / "foreign"
    write(foreign / "SKILL.md", "foreign\n")
    (skill.parent / "linked").symlink_to(foreign, target_is_directory=True)
    assert_ok(init_store(store, yes=True))
    assert skill.is_symlink() and skill.resolve() == store / "skills/real"
    assert not os.readlink(skill).startswith("/")
    assert (store / "skills/real/SKILL.md").read_text() == "adopt me\n"
    assert not (store / "skills/linked").exists()
    assert (home / ".claude/CLAUDE.md").read_text() == "operator instructions\n"
    assert not (store / "AGENTS.md").exists()


def test_adoption_collision_leaves_both_agent_and_store_unchanged(home):
    store = home / ".agents"
    write(store / "skills/clash/SKILL.md", "store\n")
    write(home / ".claude/skills/clash/SKILL.md", "agent\n")
    report = init_store(store, yes=True)
    assert report.exit_code == 1
    assert (store / "skills/clash/SKILL.md").read_text() == "store\n"
    assert (home / ".claude/skills/clash/SKILL.md").read_text() == "agent\n"
    assert not (home / ".claude/skills/clash").is_symlink()
    assert not (store / ".git").exists()


def test_interactive_instructions_move_once_without_merging(home, monkeypatch):
    store = home / ".agents"
    instruction = home / ".claude/CLAUDE.md"
    write(instruction, "My instructions\n")
    write(home / ".codex/AGENTS.md", "Other instructions\n")
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", answer)
    assert_ok(init_store(store))
    assert (store / "AGENTS.md").read_text() == "My instructions\n"
    assert "<!-- agent-hub:begin -->" in instruction.read_text()
    assert (home / ".codex/AGENTS.md").read_text() == "Other instructions\n"
    assert len(prompts) == 1
    assert_ok(init_store(store))
    assert len(prompts) == 1


def test_interactive_default_keeps_existing_instructions(home, monkeypatch):
    instruction = home / ".claude/CLAUDE.md"
    write(instruction, "Keep local\n")
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert_ok(init_store(home / ".agents"))
    assert instruction.read_text() == "Keep local\n"
    assert not (home / ".agents/AGENTS.md").exists()


def test_gitignore_and_existing_history_are_preserved_and_init_is_idempotent(home):
    store = home / ".agents"
    write(store / ".gitignore", "private/\n.DS_Store\n")
    assert_ok(init_store(store, yes=True))
    first = git(store, "rev-parse", "HEAD").stdout
    assert_ok(init_store(store, yes=True))
    assert git(store, "rev-parse", "HEAD").stdout == first
    assert (store / ".gitignore").read_text() == "private/\n.DS_Store\n*.local.*\n"


def test_remote_push_and_existing_origin_are_checked(home, tmp_path):
    remote = tmp_path / "empty.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    store = home / ".agents"
    assert_ok(init_store(store, remote=str(remote), yes=True))
    assert (
        git(store, "rev-parse", "HEAD").stdout
        == git(remote, "rev-parse", "HEAD").stdout
    )
    report = init_store(store, remote=str(tmp_path / "different.git"), yes=True)
    assert report.exit_code == 1
    assert git(store, "remote", "get-url", "origin").stdout.strip() == str(remote)


def test_missing_identity_reports_error_without_changing_inputs(home, monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        for field in ("NAME", "EMAIL"):
            monkeypatch.delenv(f"GIT_{role}_{field}", raising=False)
    write(home / ".gitconfig", "[user]\n    useConfigOnly = true\n")
    store = home / ".agents"
    write(store / "skills/keep/SKILL.md", "keep\n")
    report = init_store(store, yes=True)
    assert report.exit_code == 1
    assert "identity" in str(report.lines()) or "user.email" in str(report.lines())
    assert (store / "skills/keep/SKILL.md").read_text() == "keep\n"
    assert not (store / ".git").exists()


def test_relocated_store_keeps_external_symlink_destination(home, tmp_path):
    canonical = home / ".agents"
    outside = home / "external-skill"
    write(outside / "SKILL.md", "linked content\n")
    (canonical / "skills").mkdir(parents=True)
    (canonical / "skills/external").symlink_to(
        "../../external-skill", target_is_directory=True
    )
    store = tmp_path / "relocated"
    assert_ok(init_store(store, yes=True))
    assert (store / "skills/external").resolve() == outside
    assert (store / "skills/external/SKILL.md").read_text() == "linked content\n"


def test_overlapping_store_is_rejected_before_mutation(home):
    report = init_store(home / ".agents/nested", yes=True)
    assert report.exit_code == 1
    assert not (home / ".agents").exists()


def test_git_metadata_symlink_is_rejected_without_touching_target(home, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    git(external, "init", "-b", "main")
    store = home / ".agents"
    write(store / "keep.md", "keep\n")
    (store / ".git").symlink_to(external / ".git", target_is_directory=True)
    report = init_store(store, yes=True)
    assert report.exit_code == 1
    assert "regular Git directory" in str(report.lines())
    assert (store / ".git").is_symlink()
    assert git(external, "status", "--porcelain").stdout == ""


def test_adopted_skill_keeps_relative_external_asset_link(home):
    skill = home / ".claude/skills/linked-asset"
    write(skill / "SKILL.md", "references asset\n")
    write(home / ".claude/asset.txt", "asset\n")
    (skill / "asset.txt").symlink_to("../../asset.txt")
    assert_ok(init_store(home / ".agents", yes=True))
    assert (home / ".agents/skills/linked-asset/asset.txt").read_text() == "asset\n"


def test_existing_relative_store_skill_link_survives_repeated_init(home):
    store = home / ".agents"
    write(store / "skills/original/SKILL.md", "original\n")
    (store / "skills/alias").symlink_to("original", target_is_directory=True)
    assert_ok(init_store(store, yes=True))
    assert_ok(init_store(store, yes=True))
    assert os.readlink(store / "skills/alias") == "original"
    assert (store / "skills/alias/SKILL.md").read_text() == "original\n"
