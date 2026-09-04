"""Run skills.sh and read its provenance without owning an installer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from typing import Any

from . import config, core, gitio

INSTALL_TIMEOUT = 180
PROVENANCE_FIELDS = {
    "source": "source",
    "source_type": "sourceType",
    "source_url": "sourceUrl",
    "installed_at": "installedAt",
    "updated_at": "updatedAt",
}


@dataclass(frozen=True)
class InstallReport(core.Report):
    operation: str = "install"

    @property
    def command(self) -> str:
        return self.operation


def source_value(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source must be a non-empty string")
    value = value.strip()
    if value.startswith("-") or any(ord(character) < 32 for character in value):
        raise ValueError("source must not start with '-' or contain control characters")
    return value


def skill_names(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("names must be an array of Skill names")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("each Skill name must be a string")
        result.append(config.validate_name(value, "skill name"))
    return result


def read_provenance(repo: Path) -> dict[str, dict[str, str | None]]:
    """Read supported lock fields. Never repair or overwrite an invalid lock."""
    path = repo / ".skill-lock.json"
    if path.is_symlink():
        raise ValueError(f"{path}: lockfile must not be a symlink")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{path}: cannot read skills.sh provenance: {exc}") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] < 3
        or not isinstance(data.get("skills"), dict)
    ):
        raise ValueError(
            f"{path}: expected skills.sh lockfile version 3 or later with a skills table"
        )
    result = {}
    for name, entry in data["skills"].items():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("source"), str)
            or not entry["source"].strip()
        ):
            raise ValueError(f"{path}: invalid provenance for Skill {name!r}")
        record: dict[str, str | None] = {}
        for field, upstream in PROVENANCE_FIELDS.items():
            value = entry.get(upstream)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{path}: {name}.{upstream} must be a string")
            record[field] = value
        result[name] = record
    return result


def _git(repo: Path, *args: str) -> str:
    result = gitio.run_git(repo, *args, timeout=60)
    if result.returncode:
        raise gitio.GitCommandError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _check_store(repo: Path) -> None:
    canonical = Path.home() / ".agents"
    if not canonical.is_dir() or canonical.resolve() != repo:
        raise ValueError(
            f"skills.sh writes to ~/.agents; run 'agent-hub init --store {repo}' to bind this Store first"
        )
    if (repo / "skills").is_symlink():
        raise ValueError(f"{repo / 'skills'}: Skills directory must not be a symlink")
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ValueError("install and update require the Store Git repository root")
    git_directory = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    if any(
        (git_directory / name).exists()
        for name in (
            "rebase-merge",
            "rebase-apply",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "sequencer",
        )
    ):
        raise ValueError(
            "finish the Store's current Git operation before installing Skills"
        )
    read_provenance(repo)
    _git(repo, "var", "GIT_AUTHOR_IDENT")
    _git(repo, "var", "GIT_COMMITTER_IDENT")


def _snapshot(repo: Path) -> dict[str, str]:
    paths = list((repo / "skills").rglob("*")) if (repo / "skills").is_dir() else []
    paths.append(repo / ".skill-lock.json")
    result = {}
    for path in paths:
        if path.is_symlink():
            value = "link:" + os.readlink(path)
        elif path.is_file():
            value = (
                f"{bool(path.stat().st_mode & 0o111)}:"
                + hashlib.sha256(path.read_bytes()).hexdigest()
            )
        else:
            continue
        result[path.relative_to(repo).as_posix()] = value
    return result


def _commit_changes(repo: Path, before: dict[str, str], operation: str) -> bool:
    after = _snapshot(repo)
    tracked = set(_git(repo, "ls-files", "-z").split("\0"))
    changed = sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path) and (path in after or path in tracked)
    )
    if not changed:
        return False
    # A replacement file or link stages its removed subtree as one path.
    replacements = {path for path in changed if path in after}
    changed = [
        path
        for path in changed
        if not any(parent.as_posix() in replacements for parent in Path(path).parents)
    ]
    _git(repo, "--literal-pathspecs", "add", "-A", "--", *changed)
    pending = gitio.run_git(
        repo, "--literal-pathspecs", "diff", "--cached", "--quiet", "--", *changed
    )
    if pending.returncode == 0:
        return False
    if pending.returncode != 1:
        raise gitio.GitCommandError(pending.stderr.strip())
    _git(
        repo,
        "--literal-pathspecs",
        "commit",
        "--only",
        "-m",
        f"{operation}: Skills",
        "--",
        *changed,
    )
    return True


def _run_installer(
    args: list[str], repo: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        args,
        cwd=repo,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=INSTALL_TIMEOUT)
        except BaseException:
            # npx starts child processes; none may keep writing after unlock.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def install(repo: Path, source: str, skill: str | None = None) -> InstallReport:
    return _execute(
        repo, "install", source=source, names=[] if skill is None else [skill]
    )


def update(repo: Path, names: list[str] | None = None) -> InstallReport:
    return _execute(repo, "update", names=[] if names is None else names)


def _execute(
    repo: Path, operation: str, *, source: str = "", names: list[str]
) -> InstallReport:
    checks: list[core.StatusCheck] = []
    machine = hostname = ""
    exit_code = 1

    def note(level: str, message: str) -> None:
        checks.append(
            core.StatusCheck(kind=operation, level=level, text=core.one_line(message))
        )

    try:
        executable = shutil.which("npx")
        if executable is None:
            raise ValueError(
                "npx was not found on PATH; install Node.js with npm from https://nodejs.org, then retry"
            )
        names = skill_names(names)
        if operation == "install":
            source = source_value(source)
        repo = repo.expanduser().resolve()
        _check_store(repo)
        projection = config.load_machine_projection(repo)
        machine, hostname = projection.machine_id, projection.hostname
        before = _snapshot(repo)
        args = [executable, "-y", "skills"]
        if operation == "install":
            args.extend(["add", source, "-g", "-y"])
            if names:
                args.extend(["--skill", names[0]])
        else:
            args.extend(["update", "-g", *names])
        environment = dict(os.environ)
        # skills.sh otherwise writes its global provenance outside the Store.
        environment.pop("XDG_STATE_HOME", None)
        environment["NO_COLOR"] = "1"
        completed = _run_installer(args, repo, environment)
        for line in (completed.stdout + "\n" + completed.stderr).splitlines():
            if line.strip():
                note("", line)
        if completed.returncode:
            raise ValueError(
                f"skills.sh {operation} failed with exit {completed.returncode}; Apply and commit were not run"
            )
        _check_store(repo)
        applied = core.apply_report(config.load_machine_projection(repo))
        checks.extend(applied.checks)
        committed = _commit_changes(repo, before, operation)
        note(
            "ok",
            f"committed {operation} changes"
            if committed
            else "no Skill changes to commit",
        )
        exit_code = applied.exit_code
    except subprocess.TimeoutExpired:
        note(
            "ERROR",
            f"skills.sh {operation} timed out after {INSTALL_TIMEOUT} seconds; Apply and commit were not run; partial downloads may remain",
        )
    except config.ConfigError as exc:
        note("ERROR", str(exc))
        exit_code = 2
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        note("ERROR", str(exc))
    return InstallReport(
        machine_id=machine,
        hostname=hostname,
        repo=str(repo),
        checks=tuple(checks),
        exit_code=exit_code,
        operation=operation,
    )
