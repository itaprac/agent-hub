"""Package contract for safe Content file operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthub import files


def test_write_read_and_delete_content_file(content: Path) -> None:
    created = files.write(content, "config/skills.toml", "[alpha]\n", None)

    assert created["created"] is True
    opened = files.read(content, "config/skills.toml")
    assert opened["content"] == "[alpha]\n"
    assert opened["revision"] == created["revision"]

    deleted = files.delete(content, "config/skills.toml", opened["revision"])
    assert deleted == {"path": "config/skills.toml", "deleted": True}
    assert not (content / "config" / "skills.toml").exists()


def test_content_file_interface_rejects_application_paths(content: Path) -> None:
    with pytest.raises(files.FileError, match="outside the editable repository areas") as error:
        files.read(content, "web.py")

    assert error.value.status == 403


def test_stale_revision_does_not_replace_the_latest_file(content: Path) -> None:
    path = content / "config" / "skills.toml"
    path.write_text("[latest]\n", encoding="utf-8")

    with pytest.raises(files.FileError, match="file changed since it was opened") as error:
        files.write(content, "config/skills.toml", "[draft]\n", "0" * 64)

    assert error.value.status == 409
    assert path.read_text(encoding="utf-8") == "[latest]\n"


def test_invalid_toml_does_not_replace_the_file(content: Path) -> None:
    path = content / "config" / "skills.toml"
    path.write_text("[valid]\n", encoding="utf-8")
    revision = files.read(content, "config/skills.toml")["revision"]

    with pytest.raises(files.FileError, match="invalid TOML") as error:
        files.write(content, "config/skills.toml", "broken = [toml\n", revision)

    assert error.value.status == 422
    assert path.read_text(encoding="utf-8") == "[valid]\n"


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "skills/.hidden.md"])
def test_content_file_interface_rejects_unsafe_paths(content: Path, path: str) -> None:
    with pytest.raises(files.FileError) as error:
        files.read(content, path)

    assert error.value.status == 400


def test_atomic_write_preserves_executable_mode(content: Path) -> None:
    path = content / "skills" / "global" / "alpha" / "tool.sh"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    revision = files.read(content, "skills/global/alpha/tool.sh")["revision"]

    files.write(content, "skills/global/alpha/tool.sh", "#!/bin/sh\nexit 1\n", revision)

    assert path.stat().st_mode & 0o777 == 0o755
