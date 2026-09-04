"""Package contract for safe Content file operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import files, operations


def test_write_read_and_delete_content_file(content: Path) -> None:
    content_operations = operations.ContentOperations(content)
    (content / "hub.toml").unlink()
    created = content_operations.write_file("hub.toml", "[skills.alpha]\n", None)

    assert created["created"] is True
    opened = content_operations.read_file("hub.toml")
    assert opened["content"] == "[skills.alpha]\n"
    assert opened["revision"] == created["revision"]

    deleted = content_operations.delete_file("hub.toml", opened["revision"])
    assert deleted == {"path": "hub.toml", "deleted": True}
    assert not (content / "hub.toml").exists()


def test_content_file_interface_rejects_application_paths(content: Path) -> None:
    content_operations = operations.ContentOperations(content)
    with pytest.raises(files.FileError, match="outside the editable repository areas") as error:
        content_operations.read_file("web.py")

    assert error.value.status == 403


def test_stale_revision_does_not_replace_the_latest_file(content: Path) -> None:
    content_operations = operations.ContentOperations(content)
    path = content / "hub.toml"
    path.write_text("[skills.latest]\n", encoding="utf-8")

    with pytest.raises(files.FileError, match="file changed since it was opened") as error:
        content_operations.write_file(
            "hub.toml", "[skills.draft]\n", "0" * 64
        )

    assert error.value.status == 409
    assert path.read_text(encoding="utf-8") == "[skills.latest]\n"


def test_invalid_toml_does_not_replace_the_file(content: Path) -> None:
    content_operations = operations.ContentOperations(content)
    path = content / "hub.toml"
    path.write_text("[skills.valid]\n", encoding="utf-8")
    revision = content_operations.read_file("hub.toml")["revision"]

    with pytest.raises(files.FileError, match="invalid TOML") as error:
        content_operations.write_file(
            "hub.toml", "broken = [toml\n", revision
        )

    assert error.value.status == 422
    assert path.read_text(encoding="utf-8") == "[skills.valid]\n"


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "skills/.hidden.md"])
def test_content_file_interface_rejects_unsafe_paths(content: Path, path: str) -> None:
    content_operations = operations.ContentOperations(content)
    with pytest.raises(files.FileError) as error:
        content_operations.read_file(path)

    assert error.value.status == 400


def test_atomic_write_preserves_executable_mode(content: Path) -> None:
    content_operations = operations.ContentOperations(content)
    path = content / "skills" / "alpha" / "tool.sh"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    revision = content_operations.read_file("skills/alpha/tool.sh")["revision"]

    content_operations.write_file(
        "skills/alpha/tool.sh", "#!/bin/sh\nexit 1\n", revision
    )

    assert path.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("path", ["instructions/global/base.md", "config/agents.toml", "config/hub.toml"])
def test_legacy_paths_are_not_editable(content: Path, path: str) -> None:
    with pytest.raises(files.FileError) as error:
        files.write(content, path, "unused content", None)
    assert error.value.status == 403
