"""HTTP contract: skill creation and adoption run through the shared package."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agenthub import config, operations, projects
from conftest import git, write

SAME_ORIGIN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def post(base: str, route: str, payload: dict, headers: dict[str, str] = SAME_ORIGIN) -> dict:
    request = urllib.request.Request(
        f"{base}{route}", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def get(base: str, route: str) -> dict:
    with urllib.request.urlopen(f"{base}{route}", timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_add_skill_creates_through_the_package(server: str, content: Path) -> None:
    payload = post(server, "/api/add-skill", {"name": "gamma"})
    assert payload["command"] == "add-skill"
    assert payload["exit_code"] == 0
    assert (content / "skills" / "gamma" / "SKILL.md").is_file()
    assert payload["lines"][0]["level"] == "ok"
    assert all(set(line) == {"level", "text"} for line in payload["lines"])
    assert payload["checks"][0]["kind"] == "skill"


def test_add_skill_duplicate_is_an_error_line_with_exit_one(
    server: str, content: Path
) -> None:
    expected = operations.ContentOperations(content).add_skill("alpha").to_dict()
    payload = post(server, "/api/add-skill", {"name": "alpha"})
    assert payload == expected


def test_add_skill_unknown_project_is_rejected(server: str, content: Path) -> None:
    payload = post(server, "/api/add-skill", {"name": "gamma", "project": "nope"})
    assert payload["exit_code"] == 1
    assert not (content / "skills" / "projects" / "nope").exists()


def test_adopt_moves_through_the_package(
    server: str, content: Path, home: Path
) -> None:
    source = home / "local-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("# local-skill\n", encoding="utf-8")
    payload = post(server, "/api/adopt", {"path": str(source), "project": False})
    assert payload["command"] == "adopt"
    assert payload["exit_code"] == 0
    destination = content / "skills" / "local-skill"
    assert (destination / "SKILL.md").is_file()
    assert source.is_symlink()
    assert source.resolve() == destination.resolve()


def test_adopt_collision_is_refused(server: str, content: Path, home: Path) -> None:
    source = home / "alpha"
    source.mkdir()
    (source / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    expected = operations.ContentOperations(content).adopt(str(source)).to_dict()
    payload = post(server, "/api/adopt", {"path": str(source)})
    assert payload == expected
    assert source.is_dir() and not source.is_symlink()


def test_invalid_configuration_is_one_error_line_with_exit_two(
    server: str, content: Path
) -> None:
    (content / "hub.toml").write_text("not = [toml\n", encoding="utf-8")
    payload = post(server, "/api/add-skill", {"name": "gamma"})
    assert payload["exit_code"] == 2
    assert [line["level"] for line in payload["lines"]] == ["ERROR"]


def test_skill_mutations_require_a_browser_identity(
    server: str, content: Path, home: Path
) -> None:
    for route, payload in (
        ("/api/add-skill", {"name": "gamma"}),
        ("/api/adopt", {"path": str(home / "local-skill")}),
    ):
        with pytest.raises(urllib.error.HTTPError) as error:
            post(server, route, payload, headers={"Content-Type": "application/json"})
        assert error.value.code == 401
    assert not (content / "skills" / "gamma").exists()


def test_state_lists_the_canonical_skill_directories(server: str, content: Path) -> None:
    parent = content / "skills"
    (parent / "empty").mkdir()
    (parent / ".hidden").mkdir()
    (parent / ".hidden" / "SKILL.md").write_text("# hidden\n", encoding="utf-8")
    (parent / "dotted").mkdir()
    (parent / "dotted" / ".only-hidden").write_text("hidden\n", encoding="utf-8")
    (parent / "Bravo").mkdir()
    (parent / "Bravo" / "SKILL.md").write_text("# Bravo\n", encoding="utf-8")
    state = get(server, "/api/state")
    names = [skill["name"] for skill in state["skills"]["global"]]
    assert names == [directory.name for directory in config.skill_directories(parent)]
    assert names == ["alpha", "Bravo"]


def test_state_skill_files_hide_hidden_paths(server: str, content: Path) -> None:
    skill = content / "skills" / "alpha"
    (skill / ".cache").mkdir()
    (skill / ".cache" / "data.txt").write_text("cache\n", encoding="utf-8")
    state = get(server, "/api/state")
    [alpha] = [entry for entry in state["skills"]["global"] if entry["name"] == "alpha"]
    assert [item["name"] for item in alpha["files"]] == ["SKILL.md"]


@pytest.fixture
def web_project(project: Path) -> Path:
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.name", "Web project tests")
    git(project, "config", "user.email", "web-project@example.invalid")
    write(project / "README.md", "Project\n")
    git(project, "add", ".")
    git(project, "commit", "-qm", "project")
    git(project, "remote", "add", "origin", "https://example.invalid/team/project.git")
    return project


def test_add_skill_accepts_the_checkout_path_from_web_state(server: str, content: Path, web_project: Path) -> None:
    assert operations.ContentOperations(content).project_link(web_project).exit_code == 0
    [registered] = get(server, "/api/state")["projects"]
    payload = post(server, "/api/add-skill", {"name": "web-private", "project": registered["path"]})
    assert payload["exit_code"] == 0, payload["lines"]
    destination = content / "projects" / registered["name"] / "skills" / "web-private"
    assert (destination / "SKILL.md").is_file()
    assert (web_project / ".agents" / "skills" / "web-private").resolve() == destination
    assert git(web_project, "status", "--porcelain").stdout == ""


def test_adopt_project_scope_uses_the_source_checkout(server: str, content: Path, web_project: Path) -> None:
    source = web_project / "local-skill"
    write(source / "SKILL.md", "Private skill\n")
    payload = post(server, "/api/adopt", {"path": str(source), "project": True, "name": "adopted-private"})
    assert payload["exit_code"] == 0, payload["lines"]
    destination = content / "projects" / projects.project_slug(web_project) / "skills" / "adopted-private"
    assert source.is_symlink() and source.resolve() == destination
    assert git(web_project, "status", "--porcelain").stdout == ""
