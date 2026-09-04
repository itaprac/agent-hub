"""Move a clean v1 Content repository into the Store layout with Git history."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any

from . import config, core, gitio

AGENT_IDS = {"claude": "claude-code"}


@dataclass(frozen=True)
class MigrateReport(core.Report):
    """The result of an in-place v1 migration."""

    @property
    def command(self) -> str:
        return "migrate"


def _git(repo: Path, *args: str) -> str:
    result = gitio.run_git(repo, *args)
    if result.returncode:
        raise gitio.GitCommandError(
            result.stderr.strip() or result.stdout.strip()
            or f"git {args[0]} exited with code {result.returncode}"
        )
    return result.stdout.strip()


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _safe_path(repo: Path, path: Path) -> None:
    if not path.is_relative_to(repo):
        raise ValueError(f"migration path is outside the Store: {path}")
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError(f"{part}: migration does not follow symlinks")


def _merge(left: dict[str, Any], right: dict[str, Any], key: str) -> dict[str, Any]:
    merged = dict(left)
    for name, value in right.items():
        if name not in merged:
            merged[name] = value
        elif isinstance(merged[name], dict) and isinstance(value, dict):
            merged[name] = _merge(merged[name], value, f"{key}.{name}")
        elif merged[name] != value:
            raise ValueError(
                f"configuration collision at {key}.{name}; resolve it before migration"
            )
    return merged


def _toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def value(item: Any) -> str:
        if isinstance(item, str):
            return json.dumps(item)
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, (int, float)):
            return str(item)
        if isinstance(item, list):
            return "[" + ", ".join(value(child) for child in item) + "]"
        raise ValueError(
            f"cannot preserve unsupported TOML value {type(item).__name__}"
        )

    def table(items: dict[str, Any], path: tuple[str, ...]) -> None:
        scalars = {
            key: item for key, item in items.items() if not isinstance(item, dict)
        }
        if path and (scalars or not items):
            lines.append("[" + ".".join(json.dumps(part) for part in path) + "]")
        lines.extend(
            f"{json.dumps(key)} = {value(item)}" for key, item in scalars.items()
        )
        if scalars:
            lines.append("")
        for key, item in items.items():
            if isinstance(item, dict):
                table(item, (*path, key))

    table(data, ())
    return "\n".join(lines).rstrip() + "\n"


def migrate(repo: Path) -> MigrateReport:
    """Preflight all paths, move tracked files, and commit one migration."""
    from .projects import project_slug

    repo = repo.expanduser().resolve()
    checks: list[core.StatusCheck] = []
    machine, hostname = "", ""
    completed_moves: list[tuple[Path, Path]] = []
    backups: dict[Path, bytes] = {}
    new_hub = False
    index: Path | None = None
    index_bytes: bytes | None = None
    original_head: str | None = None
    committed = False
    mutated = False

    def note(level: str, text: str, path: Path | None = None) -> None:
        checks.append(
            core.StatusCheck(
                kind="migration",
                level=level,
                text=core.one_line(text),
                target=str(path) if path else None,
            )
        )

    try:
        machine, hostname = config.resolve_machine()
        if not (repo / ".git").is_dir() or (repo / ".git").is_symlink():
            raise ValueError(f"{repo}: migration requires a regular Git repository")
        if _git(repo, "rev-parse", "--show-toplevel") != str(repo):
            raise ValueError("migrate the Git repository root")
        if gitio.dirty(repo)[0]:
            raise ValueError(
                "Store has uncommitted changes; commit or stash them before migration"
            )
        original_head = _git(repo, "rev-parse", "HEAD")
        git_directory = repo / ".git"
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
            raise ValueError("finish the current Git operation before migration")
        configs: dict[str, list[Path]] = {}
        for name in ("hub", "skills", "projects", "agents", "peers"):
            configs[name] = [
                path
                for path in (repo / f"{name}.toml", repo / "config" / f"{name}.toml")
                if _exists(path)
            ]
            for path in configs[name]:
                _safe_path(repo, path)
        root_hub = repo / "hub.toml"
        root_data = config.load_toml(root_hub, required=False)
        new_data = {key: value for key, value in root_data.items() if key != "machines"}
        machines: dict[str, Any] = {}
        for path in configs["hub"]:
            data = config.load_toml(path)
            mapping = data.pop("machines", {})
            if not isinstance(mapping, dict):
                raise ValueError(f"{path}: machines must be a table")
            machines = _merge(machines, mapping, "machines")
            if path != root_hub:
                new_data = _merge(new_data, data, "hub")
        legacy_machine = next(
            (
                value
                for key, value in machines.items()
                if key.lower()
                in {hostname.lower(), config.short_hostname(hostname).lower()}
            ),
            machine,
        )
        if not isinstance(legacy_machine, str):
            raise ValueError("legacy Machine ID must be a string")
        if legacy_machine != machine:
            note(
                "warn",
                f"legacy Machine ID is {legacy_machine}; retain this ID in ~/.config/agent-hub/machine so Machine filters keep their meaning",
            )
        restrictions: dict[str, Any] = {}
        for path in configs["skills"]:
            data = config.load_toml(path)
            for name, fields in data.items():
                if not isinstance(fields, dict):
                    raise ValueError(f"{path}: {name} must be a Skill filter table")
                fields = dict(fields)
                if "agents" in fields:
                    if not isinstance(fields["agents"], list) or not all(
                        isinstance(item, str) for item in fields["agents"]
                    ):
                        raise ValueError(
                            f"{path}: {name}.agents must be an array of Agent IDs"
                        )
                    fields["agents"] = list(
                        dict.fromkeys(
                            AGENT_IDS.get(item, item) for item in fields["agents"]
                        )
                    )
                restrictions = _merge(restrictions, {name: fields}, "skills")
        if restrictions:
            new_data = _merge(new_data, {"skills": restrictions}, "hub")
        projects: dict[str, Any] = {}
        for path in configs["projects"]:
            projects = _merge(projects, config.load_toml(path), "projects")
        moves: list[tuple[Path, Path]] = []

        def move(source: Path, target: Path) -> None:
            _safe_path(repo, source)
            _safe_path(repo, target)
            if _exists(target) or any(target == planned for _, planned in moves):
                raise ValueError(f"migration collision at {target.relative_to(repo)}")
            if target.is_relative_to(source):
                raise ValueError(
                    f"migration destination is inside its source: {target}"
                )
            if not _git(repo, "ls-files", "--", str(source.relative_to(repo))):
                if source.name.startswith("."):
                    note("skip", f"kept untracked hidden path {source}", source)
                    return
                raise ValueError(
                    f"{source}: untracked legacy content; commit or move it before migration"
                )
            moves.append((source, target))

        global_skills = repo / "skills/global"
        _safe_path(repo, global_skills)
        if global_skills.is_dir():
            for source in sorted(global_skills.iterdir()):
                move(source, repo / "skills" / source.name)
        project_skills = repo / "skills/projects"
        _safe_path(repo, project_skills)
        if project_skills.is_dir():
            for source in sorted(project_skills.iterdir()):
                paths = projects.get(source.name, {})
                checkout = (
                    paths.get(legacy_machine) if isinstance(paths, dict) else None
                )
                slug = None
                if isinstance(checkout, str):
                    try:
                        slug = project_slug(config.expand_path(checkout))
                    except (ValueError, OSError, gitio.GitError) as exc:
                        note("warn", f"manual: cannot map project {source.name}: {exc}")
                if slug is None:
                    target = (
                        repo / "migration-unmapped/projects" / source.name / "skills"
                    )
                    move(source, target)
                    note(
                        "warn",
                        f"manual: map project {source.name} to an origin URL, then move {target} to projects/<slug>/skills; private Skills remain outside global skills/",
                    )
                else:
                    move(source, repo / "projects" / slug / "skills")
                    note(
                        "ok",
                        f"project {source.name} -> {slug}; next run agent-hub project link {checkout}",
                    )
        instructions = repo / "instructions/global"
        _safe_path(repo, instructions)
        if instructions.is_dir():
            for source in sorted(instructions.glob("*.md")):
                target = (
                    root_hub.parent / "AGENTS.md"
                    if source.name == "base.md"
                    else repo
                    / "agents"
                    / f"{AGENT_IDS.get(source.stem, source.stem)}.md"
                )
                move(source, target)
        project_instructions = repo / "instructions/projects"
        if project_instructions.exists():
            note(
                "warn",
                f"manual: keep {project_instructions}; move each instruction into its project repository when ready",
                project_instructions,
            )
        removals = [
            path
            for name, paths in configs.items()
            for path in paths
            if path != root_hub
        ]
        hub_changed = new_data != root_data or (
            bool(new_data) and not root_hub.exists()
        )
        # Preserve the source configuration history when no root hub.toml exists.
        if hub_changed and not root_hub.exists() and configs["skills"]:
            source = configs["skills"][0]
            move(source, root_hub)
            removals.remove(source)
        if not moves and not removals and not hub_changed:
            note("skip", "Store already uses the v2 layout; no migration changes")
        else:
            hub_text = _toml(new_data)
            with tempfile.TemporaryDirectory(
                prefix="agent-hub-migrate-", dir=git_directory
            ) as temporary:
                validation = Path(temporary)
                (validation / "hub.toml").write_text(hub_text, encoding="utf-8")
                config.load_settings(validation)
            _git(repo, "var", "GIT_AUTHOR_IDENT")
            _git(repo, "var", "GIT_COMMITTER_IDENT")
            index = git_directory / "index"
            if index.is_symlink():
                raise ValueError("Git index must not be a symlink")
            index_bytes = index.read_bytes() if index.exists() else None
            backups = {path: path.read_bytes() for path in removals if path.is_file()}
            for source, target in moves:
                if target == root_hub:
                    backups[source] = source.read_bytes()
            if hub_changed and root_hub.exists():
                backups[root_hub] = root_hub.read_bytes()
            new_hub = hub_changed and not root_hub.exists()
            mutated = True
            for source, target in moves:
                target.parent.mkdir(parents=True, exist_ok=True)
                _git(
                    repo,
                    "mv",
                    "--",
                    str(source.relative_to(repo)),
                    str(target.relative_to(repo)),
                )
                completed_moves.append((source, target))
                note(
                    "ok",
                    f"moved {source.relative_to(repo)} -> {target.relative_to(repo)}",
                )
            for path in removals:
                _git(repo, "rm", "--", str(path.relative_to(repo)))
                note("ok", f"removed legacy {path.relative_to(repo)}")
            if hub_changed:
                root_hub.write_text(hub_text, encoding="utf-8")
                _git(repo, "add", "--", "hub.toml")
                note(
                    "ok",
                    "wrote Store filters and settings to hub.toml; removed legacy machines mapping",
                )
            _git(repo, "commit", "-m", "migrate: v1 to v2 layout")
            committed = True
            note("ok", "committed migrate: v1 to v2 layout")
        note("skip", f"next run: agent-hub --store {repo} init")
    except (config.ConfigError, ValueError, OSError, RuntimeError) as exc:
        if mutated and not committed:
            try:
                for source, target in reversed(completed_moves):
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(source)
                if new_hub and root_hub.exists():
                    root_hub.unlink()
                for path, original_bytes in backups.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original_bytes)
                if index is not None:
                    if index_bytes is None:
                        index.unlink(missing_ok=True)
                    else:
                        index.write_bytes(index_bytes)
                if original_head is not None:
                    # Commit hooks can rewrite moved files before rejecting a
                    # commit. The initial checkout was clean, so restore its
                    # tracked bytes and modes after restoring the original index.
                    _git(
                        repo, "-c", f"core.worktree={repo}", "restore",
                        f"--source={original_head}", "--worktree", "--", ".",
                    )
            except (OSError, gitio.GitError) as rollback_error:
                note(
                    "ERROR", f"migration rollback needs manual repair: {rollback_error}"
                )
        note("ERROR", str(exc))
    return MigrateReport(
        machine_id=machine,
        hostname=hostname,
        repo=str(repo),
        checks=tuple(checks),
        exit_code=int(any(check.level == "ERROR" for check in checks)),
    )
