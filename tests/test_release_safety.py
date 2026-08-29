"""Checks for public App files and Example Content."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_CONTENT_DIRS = frozenset({"skills", "instructions", "config"})
FORBIDDEN_PATTERNS = (
    (
        "private account name",
        r"(?<![A-Za-z0-9_])" + "s" + "rv" + r"(?![A-Za-z0-9_])",
    ),
    ("private personal name", "szy" + "mon"),
    ("private surname", "Śnie" + "gowski"),
    ("private handle", "szympon" + "biceps"),
    ("private tailnet name", "tail" + "89edd4"),
    ("private fleet hostname", "mini" + r"\.tail"),
    ("private fleet hostname", "szy" + "mons" + r"-air"),
    ("private fleet hostname", "Szy" + "mons" + r"-MacBook-Air"),
    ("private fleet address", r"100\.124\." + r"218\.15"),
    ("private fleet address", r"100\.70\." + r"243\.128"),
    ("private home path", "/Users/" + "s" + "rv"),
    ("private home path", "/Users/" + "ita" + "prac"),
)


def _tracked_regular_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = []
    for raw_path in result.stdout.decode("utf-8").split("\0"):
        if not raw_path:
            continue
        relative = Path(raw_path)
        if relative.parts[0] in PRIVATE_CONTENT_DIRS:
            continue
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            continue
        paths.append(path)
    return paths


def test_tracked_app_files_contain_no_private_artifacts() -> None:
    patterns = [
        (label, re.compile(pattern, re.IGNORECASE))
        for label, pattern in FORBIDDEN_PATTERNS
    ]
    failures = []
    for path in _tracked_regular_files():
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in patterns:
                if pattern.search(line):
                    failures.append(
                        f"{path.relative_to(ROOT)}:{line_number}: {label}"
                    )
    assert not failures, "Private artifacts found:\n" + "\n".join(failures)


def test_example_content_has_one_valid_skill_and_instruction() -> None:
    example = ROOT / "example-content"
    skill_files = sorted((example / "skills").glob("**/SKILL.md"))
    instruction_files = sorted((example / "instructions").glob("**/*.md"))

    assert [path.relative_to(example).as_posix() for path in skill_files] == [
        "skills/global/example/SKILL.md"
    ]
    assert [path.relative_to(example).as_posix() for path in instruction_files] == [
        "instructions/global/base.md"
    ]

    skill = skill_files[0].read_text(encoding="utf-8")
    assert re.match(
        r"\A---\nname: example\ndescription: .+\n---\n\n# Example\n",
        skill,
    )
    assert instruction_files[0].read_text(encoding="utf-8").strip()

    for config_path in sorted((example / "config").glob("*.toml")):
        with config_path.open("rb") as handle:
            assert isinstance(tomllib.load(handle), dict)
