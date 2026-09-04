"""Create a Store while preserving existing local Skills and instructions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import core, gitio
from .config import ConfigError, load_settings, resolve_machine, skill_directories


@dataclass(frozen=True)
class InitReport(core.Report):
    """The result of Store initialization."""

    @property
    def command(self) -> str:
        return "init"


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _run_git(repo: Path, *args: str):
    if args[0] not in {"init", "clone"}:
        args = ("--git-dir", str(repo / ".git"), "--work-tree", str(repo), *args)
    return gitio.run_git(repo, *args, timeout=60)


def _git(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args)
    if result.returncode:
        raise gitio.GitCommandError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _merge_lock(left: Any, right: Any, key: str = ".skill-lock.json") -> Any:
    if isinstance(left, dict) and isinstance(right, dict):
        result = dict(left)
        for name, value in right.items():
            result[name] = (
                _merge_lock(result[name], value, f"{key}.{name}")
                if name in result
                else value
            )
        return result
    if left == right:
        return left
    raise ValueError(
        f"name clash in {key}; keep both Stores unchanged and resolve it first"
    )


def _copy_entry(
    source: Path, target: Path, source_root: Path, final_root: Path, stage_root: Path
) -> None:
    if source.is_symlink():
        destination = source.resolve(strict=False)
        if destination.is_relative_to(source_root):
            destination = final_root / destination.relative_to(source_root)
        # Store links stay correct when ~/.agents moves to another directory.
        final_parent = final_root / target.parent.relative_to(stage_root)
        target.symlink_to(
            os.path.relpath(destination, final_parent),
            target_is_directory=source.is_dir(),
        )
    elif source.is_dir():
        target.mkdir()
        for entry in source.iterdir():
            _copy_entry(entry, target / entry.name, source_root, final_root, stage_root)
        shutil.copystat(source, target)
    else:
        shutil.copy2(source, target)


def _merge_tree(
    source: Path,
    stage: Path,
    final_root: Path,
    relative: Path = Path(),
    stage_root: Path | None = None,
    source_root: Path | None = None,
) -> None:
    """Merge in staging; a collision never changes either input directory."""
    stage_root = stage if stage_root is None else stage_root
    source_root = source if source_root is None else source_root
    for entry in source.iterdir():
        destination = stage / entry.name
        location = relative / entry.name
        if not _exists(destination):
            _copy_entry(entry, destination, source_root, final_root, stage_root)
            continue
        if (
            entry.is_symlink()
            or destination.is_symlink()
            or (len(location.parts) == 2 and location.parts[0] == "skills")
            or location == Path(".git")
        ):
            raise ValueError(
                f"name clash at {location}; existing files were not changed"
            )
        if entry.is_dir() and destination.is_dir():
            _merge_tree(
                entry, destination, final_root, location, stage_root, source_root
            )
        elif (
            location == Path(".skill-lock.json")
            and entry.is_file()
            and destination.is_file()
        ):
            merged = _merge_lock(
                json.loads(destination.read_text()), json.loads(entry.read_text())
            )
            destination.write_text(
                json.dumps(merged, indent=2) + "\n", encoding="utf-8"
            )
        elif (
            location == Path(".gitignore") and entry.is_file() and destination.is_file()
        ):
            existing = destination.read_text(encoding="utf-8")
            additions = [
                line
                for line in entry.read_text(encoding="utf-8").splitlines()
                if line not in existing.splitlines()
            ]
            destination.write_text(
                existing.rstrip("\n") + "\n" + "\n".join(additions) + "\n",
                encoding="utf-8",
            )
        else:
            raise ValueError(
                f"name clash at {location}; existing files were not changed"
            )


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, OSError):
        return False


def _safe_file(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(
            f"{path}: expected a regular file; existing content was not changed"
        )


def _install_stage(stage: Path, repo: Path, canonical: Path) -> None:
    """Replace directories only after all content and Git preflight succeeds."""
    backups: list[tuple[Path, Path]] = []
    installed = False
    linked = False
    try:
        for original in (repo, canonical) if repo != canonical else (repo,):
            if original == canonical and canonical.is_symlink():
                continue
            if original.exists():
                backup = Path(
                    tempfile.mkdtemp(
                        prefix=f".{original.name}-backup-", dir=original.parent
                    )
                )
                backup.rmdir()
                original.rename(backup)
                backups.append((original, backup))
        stage.rename(repo)
        installed = True
        if repo != canonical and not canonical.is_symlink():
            canonical.symlink_to(repo, target_is_directory=True)
            linked = True
    except OSError:
        if linked:
            canonical.unlink()
        if installed:
            shutil.rmtree(repo)
        for original, backup in reversed(backups):
            backup.rename(original)
        raise
    else:
        for _, backup in backups:
            shutil.rmtree(backup)


def init_store(
    repo: Path,
    *,
    from_url: str | None = None,
    remote: str | None = None,
    yes: bool = False,
) -> InitReport:
    """Initialize the Store. The caller must hold the operation lock."""
    checks: list[core.StatusCheck] = []
    machine, hostname = "", ""
    canonical = Path.home().resolve() / ".agents"
    stage: Path | None = None

    def note(level: str, text: str, target: Path | None = None) -> None:
        checks.append(
            core.StatusCheck(
                kind="init",
                level=level,
                text=text,
                target=str(target) if target else None,
            )
        )

    try:
        machine, hostname = resolve_machine()
        repo = repo.expanduser().resolve(strict=False)
        canonical_target = canonical.resolve(strict=False)
        if canonical.is_symlink() and canonical_target != repo:
            raise ValueError(
                f"{canonical} already points to {canonical_target}; keep that link or choose that Store"
            )
        if repo != canonical and not canonical.is_symlink():
            if repo.is_relative_to(canonical) or canonical.is_relative_to(repo):
                raise ValueError("the Store and ~/.agents must not contain each other")
        for path in {repo, canonical}:
            if _exists(path) and not path.is_dir():
                raise ValueError(f"{path}: expected a directory")
        for path in {repo, canonical}:
            metadata = path / ".git"
            if _exists(metadata) and (metadata.is_symlink() or not metadata.is_dir()):
                raise ValueError(
                    f"{metadata}: init requires a regular Git directory, not a linked worktree"
                )
            if metadata.is_dir() and any(
                item.is_symlink() for item in metadata.rglob("*")
            ):
                raise ValueError(f"{metadata}: Git metadata must not contain symlinks")
        if from_url and any(
            (path / ".git").exists() for path in {repo, canonical} if path.exists()
        ):
            raise ValueError(
                "--from requires a Store without an existing Git repository"
            )
        repo.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".agent-hub-init-", dir=repo.parent))
        if from_url:
            _git(repo.parent, "clone", "--", from_url, str(stage))
        sources = [repo] if repo.exists() else []
        if canonical.exists() and canonical_target != repo:
            sources.append(canonical)
        for source in sources:
            _merge_tree(source, stage, repo)
        settings = load_settings(stage)
        detected = [agent for agent in settings["agents"].values() if agent.detected]
        for agent in detected:
            note("ok", f"detected {agent.name} ({agent.id})")
        adoptions: list[tuple[Path, Path]] = []
        seen: set[Path] = set()
        for agent in detected:
            if agent.universal or agent.skills_global is None:
                continue
            for source in skill_directories(agent.skills_global):
                if source.is_symlink() or source.resolve() in seen:
                    continue
                if source.resolve().is_relative_to(
                    repo
                ) or source.resolve().is_relative_to(canonical_target):
                    continue
                seen.add(source.resolve())
                if not yes and not _confirm(f"Adopt {source} into the Store?"):
                    note("skip", f"kept {source}")
                    continue
                target = stage / "skills" / source.name
                if _exists(target):
                    raise ValueError(
                        f"name clash for Skill {source.name}: {source} and {repo / 'skills' / source.name}"
                    )
                if target.parent.is_symlink():
                    raise ValueError("Store skills directory must not be a symlink")
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_entry(
                    source,
                    target,
                    source.resolve(),
                    repo / "skills" / source.name,
                    target,
                )
                adoptions.append((source, repo / "skills" / source.name))
        instructions: list[tuple[Path, str]] = []
        instructions_source = stage / "AGENTS.md"
        if not yes and not _exists(instructions_source):
            for agent in detected:
                target = agent.instructions_global
                if target is None or not target.is_file() or target.is_symlink():
                    continue
                text = core.read_text_preserving_newlines(target)
                if not text.strip() or "<!-- agent-hub:" in text:
                    continue
                if _confirm(f"Move instructions from {target} into Store AGENTS.md?"):
                    instructions_source.write_text(text, encoding="utf-8", newline="")
                    rendered, _ = core.render_managed(None, text.rstrip("\n"))
                    instructions.append((target, rendered))
                    break
        ignore = stage / ".gitignore"
        _safe_file(ignore)
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        missing = [
            pattern
            for pattern in (".DS_Store", "*.local.*")
            if pattern not in existing.splitlines()
        ]
        if missing:
            ignore.write_text(
                existing
                + ("\n" if existing and not existing.endswith("\n") else "")
                + "\n".join(missing)
                + "\n",
                encoding="utf-8",
            )
        if not (stage / ".git").exists():
            _git(stage, "init", "-b", "main")
        if remote:
            origin = _run_git(stage, "remote", "get-url", "origin")
            if origin.returncode == 0 and origin.stdout.strip() != remote:
                raise ValueError(
                    f"origin already points to {origin.stdout.strip()}; choose that remote or change it explicitly"
                )
            if origin.returncode:
                _git(stage, "remote", "add", "origin", remote)
        _git(stage, "add", "-A")
        if _run_git(stage, "diff", "--cached", "--quiet").returncode:
            _git(stage, "commit", "-m", f"init: {machine}")
        _install_stage(stage, repo, canonical)
        stage = None
        note("ok", f"Store ready at {repo}", repo)
        for source, target in adoptions:
            backup = Path(
                tempfile.mkdtemp(prefix=f".{source.name}-adopt-", dir=source.parent)
            )
            backup.rmdir()
            source.rename(backup)
            try:
                source.symlink_to(
                    os.path.relpath(target, source.parent), target_is_directory=True
                )
            except OSError:
                backup.rename(source)
                raise
            shutil.rmtree(backup)
            note("link", f"adopted {source} -> {target}", source)
        for target, rendered in instructions:
            target.write_text(rendered, encoding="utf-8", newline="")
            note(
                "render", f"moved instructions from {target} to Store AGENTS.md", target
            )
        if remote:
            _git(repo, "push", "-u", "origin", "HEAD")
            note("ok", "pushed Store to origin")
        elif not gitio.remotes(repo):
            note(
                "skip",
                f"To set an origin: git -C {repo} remote add origin URL; then git -C {repo} push -u origin HEAD",
            )
        else:
            note(
                "ok",
                "Store origin is configured; run agent-hub sync to publish local changes",
            )
        note("skip", "On the next Machine: agent-hub init --from URL")
    except (ConfigError, ValueError, OSError, RuntimeError) as exc:
        note("ERROR", core.one_line(str(exc)))
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return InitReport(
        machine_id=machine,
        hostname=hostname,
        repo=str(repo),
        checks=tuple(checks),
        exit_code=int(any(check.level == "ERROR" for check in checks)),
    )
