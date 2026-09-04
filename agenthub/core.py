"""Fleet operations: managed targets, apply, status, sync, and skill authoring."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, TypeVar

from . import gitio
from .config import (
    ConfigError,
    InstructionTarget,
    MachineProjection,
    SkillTarget,
    expand_path,
    load_machine_projection,
    validate_name,
)

BEGIN_MARKER = "<!-- agent-hub:begin -->"
END_MARKER = "<!-- agent-hub:end -->"
MANAGED_NOTICE = (
    "<!-- Managed by agent-hub. Edit in the content repo; local edits are overwritten. -->"
)

PROBLEM_LEVELS = frozenset({"MISSING", "DRIFT", "STALE", "ERROR"})


def iter_orphaned_skill_links(projection: MachineProjection):
    """Inspect projected symlink directories for entries no longer selected."""
    repo_skills = (projection.repo / "skills").resolve(strict=False)
    for managed in projection.managed_skill_directories:
        if not managed.path.is_dir():
            continue
        for entry in sorted(managed.path.iterdir(), key=lambda item: item.name):
            if not entry.is_symlink() or entry.name in managed.expected_entries:
                continue
            try:
                destination = entry.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if destination.is_relative_to(repo_skills):
                yield entry, destination


def prune_skill_links(projection: MachineProjection, dry_run: bool) -> list[StatusCheck]:
    checks = []
    for link, destination in iter_orphaned_skill_links(projection):
        if not dry_run:
            link.unlink()
        verb = "would remove" if dry_run else "remove"
        checks.append(
            StatusCheck(
                kind="prune",
                level="prune",
                text=f"{verb} {link} -> {destination}",
                target=str(link),
            )
        )
    return checks


def target_label(item: SkillTarget | InstructionTarget) -> str:
    scope = "global" if item.project is None else f"project {item.project}"
    name = f"/{item.name}" if isinstance(item, SkillTarget) else ""
    return f"{item.agent} {scope}{name}: {item.target}"


# ----------------------------------------------------------------------- files

def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): hash_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def same_symlink(target: Path, source: Path) -> bool:
    if not target.is_symlink():
        return False
    try:
        return target.resolve(strict=False) == source.resolve(strict=False)
    except OSError:
        return False


def managed_block(content: str) -> str:
    return f"{BEGIN_MARKER}\n{MANAGED_NOTICE}\n{content}\n{END_MARKER}"


def read_text_preserving_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def render_managed(existing: str | None, content: str) -> tuple[str, bool]:
    block = managed_block(content)
    if existing is None or existing == "":
        return block + "\n", False

    begin_count = existing.count(BEGIN_MARKER)
    end_count = existing.count(END_MARKER)
    begin_prefix_count = existing.count("<!-- agent-hub:begin")
    if begin_prefix_count != begin_count:
        return existing, True
    if begin_count == 0 and end_count == 0:
        separator = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
        return existing + separator + block + "\n", False
    if begin_count != 1 or end_count != 1:
        return existing, True

    begin = existing.index(BEGIN_MARKER)
    end_position = existing.find(END_MARKER, begin + len(BEGIN_MARKER))
    if end_position < 0:
        return existing, True
    end = end_position + len(END_MARKER)
    return existing[:begin] + block + existing[end:], False


# --------------------------------------------------------------------- reports

@dataclasses.dataclass(frozen=True)
class StatusCheck:
    """One reported observation about a managed target, a project, or git."""

    kind: str
    level: str
    text: str
    agent: str | None = None
    project: str | None = None
    name: str | None = None
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Report:
    """The structured result of one command, shared by the CLI and the web application."""

    machine_id: str
    hostname: str
    repo: str
    checks: tuple[StatusCheck, ...]
    exit_code: int

    @property
    def command(self) -> str:
        raise NotImplementedError

    @property
    def problems(self) -> int:
        return sum(1 for check in self.checks if check.level in PROBLEM_LEVELS)

    def lines(self) -> list[dict[str, str]]:
        """The command output that the CLI prints and the browser renders."""
        return [{"level": check.level, "text": check.text} for check in self.checks]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "problems": self.problems,
            "machine_id": self.machine_id,
            "hostname": self.hostname,
            "repo": self.repo,
            "lines": self.lines(),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclasses.dataclass(frozen=True)
class StatusReport(Report):
    """The result of one status run."""

    @property
    def command(self) -> str:
        return "status"


@dataclasses.dataclass(frozen=True)
class ApplyReport(Report):
    """The result of one apply run."""

    dry_run: bool = False

    @property
    def command(self) -> str:
        # The browser shows this string verbatim, exactly as the CLI was invoked.
        return "--dry-run apply" if self.dry_run else "apply"

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "dry_run": self.dry_run}


@dataclasses.dataclass(frozen=True)
class AddSkillReport(Report):
    """The result of one skill creation."""

    @property
    def command(self) -> str:
        return "add-skill"


@dataclasses.dataclass(frozen=True)
class AdoptReport(Report):
    """The result of one skill adoption."""

    @property
    def command(self) -> str:
        return "adopt"


@dataclasses.dataclass(frozen=True)
class SyncReport(Report):
    """The result of one sync run."""

    dry_run: bool = False

    @property
    def command(self) -> str:
        # The browser shows this string verbatim, exactly as the CLI was invoked.
        return "--dry-run sync" if self.dry_run else "sync"

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "dry_run": self.dry_run}


# ----------------------------------------------------------------------- apply

def skill_fields(item: SkillTarget) -> dict[str, Any]:
    return {
        "kind": "skill",
        "agent": item.agent,
        "project": item.project,
        "name": item.name,
        "target": str(item.target),
    }


def apply_symlink(item: SkillTarget, dry_run: bool) -> StatusCheck:
    source = item.source
    target = item.target
    label = target_label(item)
    fields = skill_fields(item)
    if same_symlink(target, source):
        return StatusCheck(level="ok", text=label, **fields)
    if target.is_symlink():
        if not dry_run:
            target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
        return StatusCheck(level="link", text=f"replace {label} -> {source}", **fields)
    if target.exists():
        return StatusCheck(
            level="DRIFT", text=f"{label} is not a symlink; run: hub adopt {target}", **fields
        )
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    return StatusCheck(level="link", text=f"{label} -> {source}", **fields)


def remove_extra_copy_paths(source: Path, target: Path) -> None:
    target_paths = sorted(target.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in target_paths:
        relative = path.relative_to(target)
        source_path = source / relative
        source_exists = source_path.exists() or source_path.is_symlink()
        same_kind = (
            source_exists
            and not path.is_symlink()
            and (
                (path.is_dir() and source_path.is_dir())
                or (path.is_file() and source_path.is_file())
            )
        )
        if same_kind:
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def apply_copy(item: SkillTarget, dry_run: bool) -> StatusCheck:
    source = item.source
    target = item.target
    label = target_label(item)
    fields = skill_fields(item)
    if target.is_dir() and not target.is_symlink() and tree_hashes(source) == tree_hashes(target):
        return StatusCheck(level="ok", text=label, **fields)
    if (target.exists() or target.is_symlink()) and (not target.is_dir() or target.is_symlink()):
        return StatusCheck(
            level="DRIFT", text=f"{label} cannot be copied over a non-directory target", **fields
        )
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        remove_extra_copy_paths(source, target)
        shutil.copytree(source, target, dirs_exist_ok=True)
    return StatusCheck(level="copy", text=f"{label} <- {source}", **fields)


def apply_instruction(item: InstructionTarget, dry_run: bool) -> StatusCheck:
    target = item.target
    label = target_label(item)
    fields: dict[str, Any] = {
        "kind": "instruction",
        "agent": item.agent,
        "project": item.project,
        "target": str(target),
    }
    if target.is_symlink() and not target.exists():
        return StatusCheck(level="DRIFT", text=f"{label} is a broken symlink", **fields)
    if target.exists() and not target.is_file():
        return StatusCheck(level="DRIFT", text=f"{label} is not a regular file", **fields)
    if target.exists():
        existing = read_text_preserving_newlines(target)
    else:
        existing = None
    rendered, malformed = render_managed(existing, item.content)
    if malformed:
        return StatusCheck(
            level="DRIFT", text=f"{label} has malformed or duplicate managed markers", **fields
        )
    if existing == rendered:
        return StatusCheck(level="ok", text=label, **fields)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
    return StatusCheck(level="render", text=label, **fields)


def apply_report(projection: MachineProjection, dry_run: bool = False) -> ApplyReport:
    """Deploy every managed target and return the structured result."""
    checks: list[StatusCheck] = [
        StatusCheck(
            kind="project",
            level="skip",
            text=f"project {project.name}: {project.reason}",
        )
        for project in projection.projects
        if not project.available
    ]
    checks.extend(prune_skill_links(projection, dry_run))
    for item in projection.skill_targets:
        checks.append(SKILL_APPLIERS[item.mode](item, dry_run))
    for instruction in projection.instruction_targets:
        checks.append(apply_instruction(instruction, dry_run))
    problems = sum(1 for check in checks if check.level in PROBLEM_LEVELS)
    return ApplyReport(
        machine_id=projection.machine_id,
        hostname=projection.hostname,
        repo=str(projection.repo),
        checks=tuple(checks),
        exit_code=1 if problems else 0,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------- status

def check_copy_skill(item: SkillTarget) -> StatusCheck:
    source = item.source
    target = item.target
    label = target_label(item)
    fields = skill_fields(item)
    if not target.exists() and not target.is_symlink():
        return StatusCheck(level="MISSING", text=label, **fields)
    if target.is_symlink() or not target.is_dir():
        return StatusCheck(
            level="DRIFT",
            text=f"{label} is not a regular directory for copy mode",
            **fields,
        )
    if tree_hashes(source) != tree_hashes(target):
        return StatusCheck(
            level="DRIFT", text=f"{label} differs from repository content", **fields
        )
    return StatusCheck(level="ok", text=label, **fields)


def check_symlink_skill(item: SkillTarget) -> StatusCheck:
    source = item.source
    target = item.target
    label = target_label(item)
    fields = skill_fields(item)
    if same_symlink(target, source):
        return StatusCheck(level="ok", text=label, **fields)
    if target.is_symlink():
        try:
            destination = os.readlink(target)
        except OSError:
            destination = "unreadable"
        return StatusCheck(
            level="DRIFT", text=f"{label} points to {destination}, expected {source}", **fields
        )
    if target.exists():
        return StatusCheck(level="DRIFT", text=f"{label} is not a symlink", **fields)
    return StatusCheck(level="MISSING", text=label, **fields)


SKILL_APPLIERS = {"copy": apply_copy, "symlink": apply_symlink}
SKILL_CHECKERS = {"copy": check_copy_skill, "symlink": check_symlink_skill}


def check_skill(item: SkillTarget) -> StatusCheck:
    return SKILL_CHECKERS[item.mode](item)


def check_instruction(item: InstructionTarget) -> StatusCheck:
    target = item.target
    label = target_label(item)
    fields: dict[str, Any] = {
        "kind": "instruction",
        "agent": item.agent,
        "project": item.project,
        "name": None,
        "target": str(target),
    }
    if not target.exists():
        return StatusCheck(level="MISSING", text=label, **fields)
    if not target.is_file():
        return StatusCheck(level="DRIFT", text=f"{label} is not a regular file", **fields)
    existing = read_text_preserving_newlines(target)
    rendered, malformed = render_managed(existing, item.content)
    if malformed or BEGIN_MARKER not in existing or END_MARKER not in existing:
        return StatusCheck(
            level="STALE", text=f"{label} has missing or malformed managed markers", **fields
        )
    if existing != rendered:
        return StatusCheck(level="STALE", text=f"{label} managed content is out of date", **fields)
    return StatusCheck(level="ok", text=label, **fields)


def one_line(text: str) -> str:
    """Git reports errors over several lines; one check is always one output line."""
    return " ".join(text.split())


def check_git(repo: Path) -> list[StatusCheck]:
    def git_check(level: str, text: str) -> StatusCheck:
        return StatusCheck(kind="git", level=level, text=text, target=str(repo))

    if not gitio.is_repository(repo):
        return [git_check("ERROR", f"git: {repo} is not a git repository")]
    try:
        dirty, entries = gitio.dirty(repo)
    except RuntimeError as exc:
        return [git_check("ERROR", f"git: {one_line(str(exc))}")]

    checks = [
        git_check("DRIFT", f"git: working tree has {len(entries)} uncommitted change(s)")
        if dirty
        else git_check("ok", "git: working tree clean")
    ]

    upstream = gitio.upstream(repo)
    if not upstream:
        checks.append(git_check("skip", "git: no upstream configured"))
        return checks
    try:
        ahead, behind = gitio.divergence(repo)
    except gitio.GitOutputError as exc:
        checks.append(git_check("ERROR", f"git: unexpected rev-list output: {one_line(str(exc))}"))
        return checks
    except gitio.GitCommandError as exc:
        checks.append(git_check("ERROR", f"git: cannot compare with {upstream}: {one_line(str(exc))}"))
        return checks
    if ahead or behind:
        checks.append(git_check("DRIFT", f"git: {ahead} ahead, {behind} behind {upstream}"))
    else:
        checks.append(git_check("ok", f"git: even with {upstream}"))
    return checks


def status_report(projection: MachineProjection) -> StatusReport:
    """Inspect projects, managed targets, and git, and return the structured result."""
    checks: list[StatusCheck] = [
        StatusCheck(
            kind="project",
            level="skip",
            text=f"project {project.name}: {project.reason}",
        )
        for project in projection.projects
        if not project.available
    ]
    for link, destination in iter_orphaned_skill_links(projection):
        checks.append(
            StatusCheck(
                kind="orphan",
                level="STALE",
                text=f"orphaned skill symlink: {link} -> {destination}",
                target=str(link),
            )
        )
    checks.extend(check_skill(item) for item in projection.skill_targets)
    checks.extend(check_instruction(item) for item in projection.instruction_targets)
    checks.extend(check_git(projection.repo))
    problems = sum(1 for check in checks if check.level in PROBLEM_LEVELS)
    return StatusReport(
        machine_id=projection.machine_id,
        hostname=projection.hostname,
        repo=str(projection.repo),
        checks=tuple(checks),
        exit_code=1 if problems else 0,
    )


# ------------------------------------------------------------------------ sync

def git_action(
    repo: Path, args: list[str], description: str
) -> tuple[list[StatusCheck], bool]:
    result = gitio.run_git(repo, *args)
    # Raw git stdout keeps an empty level; both fronts render it without a prefix.
    checks = [
        StatusCheck(kind="git", level="", text=line, target=str(repo))
        for line in (raw.strip() for raw in result.stdout.splitlines())
        if line
    ]
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} exited {result.returncode}"
        checks.append(
            StatusCheck(
                kind="git",
                level="ERROR",
                text=one_line(f"{description}: {detail}"),
                target=str(repo),
            )
        )
        return checks, False
    checks.append(
        StatusCheck(kind="git", level="ok", text=description, target=str(repo))
    )
    return checks, True


def sync_report(projection: MachineProjection, dry_run: bool = False) -> SyncReport:
    """Commit, pull, apply, and push, then return the structured result."""
    repo = projection.repo
    # The report keeps the identity that made the commit, not the reloaded one.
    machine_id = projection.machine_id
    hostname = projection.hostname
    checks: list[StatusCheck] = []

    def report(exit_code: int) -> SyncReport:
        return SyncReport(
            machine_id=machine_id,
            hostname=hostname,
            repo=str(repo),
            checks=tuple(checks),
            exit_code=exit_code,
            dry_run=dry_run,
        )

    def git_check(level: str, text: str) -> StatusCheck:
        return StatusCheck(kind="git", level=level, text=text, target=str(repo))

    if not gitio.is_repository(repo):
        checks.append(git_check("ERROR", f"git: {repo} is not a git repository"))
        return report(1)
    try:
        dirty, _ = gitio.dirty(repo)
    except RuntimeError as exc:
        checks.append(git_check("ERROR", f"git: {one_line(str(exc))}"))
        return report(1)

    if dirty:
        message = f"hub sync: {machine_id}"
        if dry_run:
            checks.append(
                git_check("commit", f"would run git add -A and commit: {message}")
            )
        else:
            action_checks, ok = git_action(repo, ["add", "-A"], "git add -A")
            checks.extend(action_checks)
            if not ok:
                return report(1)
            action_checks, ok = git_action(
                repo, ["commit", "-m", message], f"git commit: {message}"
            )
            checks.extend(action_checks)
            if not ok:
                return report(1)
    else:
        checks.append(git_check("ok", "git: nothing to commit"))

    remotes = gitio.remotes(repo)
    upstream = gitio.upstream(repo)
    if remotes and upstream:
        if dry_run:
            checks.append(
                git_check("pull", f"would run git pull --rebase from {upstream}")
            )
        else:
            action_checks, ok = git_action(
                repo, ["pull", "--rebase"], "git pull --rebase"
            )
            checks.extend(action_checks)
            if not ok:
                checks.append(
                    git_check(
                        "ERROR",
                        "sync stopped after pull/rebase failure; apply and push were not run",
                    )
                )
                return report(1)
    elif not remotes:
        checks.append(
            git_check("skip", "git: no remote configured; pull and push disabled")
        )
    else:
        checks.append(
            git_check(
                "skip", "git: remote exists but no upstream is configured; pull disabled"
            )
        )

    # The pull may have changed config/, so apply must not use the context
    # loaded before it (a freshly pulled skills.toml restriction would be
    # invisible while the new skill directory is already on disk).
    if not dry_run:
        try:
            projection = load_machine_projection(repo)
        except ConfigError as exc:
            checks.append(
                StatusCheck(
                    kind="config",
                    level="ERROR",
                    text=one_line(str(exc)),
                    target=str(repo),
                )
            )
            return report(1)

    applied = apply_report(projection, dry_run=dry_run)
    checks.extend(applied.checks)

    push_ok = True
    if remotes:
        if dry_run:
            checks.append(git_check("push", "would run git push"))
        else:
            action_checks, push_ok = git_action(repo, ["push"], "git push")
            checks.extend(action_checks)
    return report(1 if applied.exit_code or not push_ok else 0)


# ---------------------------------------------------------------------- skills

SKILL_TEMPLATE = (
    '---\nname: {name}\ndescription: "TODO: describe when to use this skill."\n---\n\n'
    "# {name}\n"
)


SkillReportT = TypeVar("SkillReportT", bound=Report)


def skill_check(level: str, text: str, **fields: Any) -> StatusCheck:
    return StatusCheck(kind="skill", level=level, text=text, **fields)


def build_skill_report(
    cls: type[SkillReportT], projection: MachineProjection, *checks: StatusCheck
) -> SkillReportT:
    problems = sum(1 for check in checks if check.level in PROBLEM_LEVELS)
    return cls(
        machine_id=projection.machine_id,
        hostname=projection.hostname,
        repo=str(projection.repo),
        checks=checks,
        exit_code=1 if problems else 0,
    )


def skill_destination(
    projection: MachineProjection, name: str, project: str | None, action: str
) -> tuple[Path | None, StatusCheck | None]:
    """The repository directory for a named skill, or the check that rejects it."""
    try:
        name = validate_name(name, "skill name")
    except ValueError as exc:
        return None, skill_check("ERROR", str(exc), name=name, project=project)
    if project is None:
        return projection.repo / "skills" / "global" / name, None
    if not projection.has_project(project):
        return None, skill_check(
            "ERROR",
            f"{projection.projects_config_path}: key '{project}' is missing; "
            f"add the project before {action}",
            name=name,
            project=project,
        )
    return projection.repo / "skills" / "projects" / project / name, None


def add_skill_report(
    projection: MachineProjection, name: str, project: str | None
) -> AddSkillReport:
    """Create a skill skeleton and return the structured result."""

    def report(*checks: StatusCheck) -> AddSkillReport:
        return build_skill_report(AddSkillReport, projection, *checks)

    destination, rejected = skill_destination(
        projection, name, project, "creating a project skill"
    )
    if rejected is not None:
        return report(rejected)
    assert destination is not None
    fields = {"name": destination.name, "project": project, "target": str(destination)}
    if destination.exists() or destination.is_symlink():
        return report(skill_check("ERROR", f"skill already exists: {destination}", **fields))
    destination.mkdir(parents=True)
    skill_file = destination / "SKILL.md"
    skill_file.write_text(SKILL_TEMPLATE.format(name=destination.name), encoding="utf-8")
    return report(skill_check("ok", f"created {skill_file}", **fields))


def adopt_skill_report(
    projection: MachineProjection,
    path_value: str,
    project: str | None,
    explicit_name: str | None,
) -> AdoptReport:
    """Move an existing skill into the Content repository and return the structured result."""

    def report(*checks: StatusCheck) -> AdoptReport:
        return build_skill_report(AdoptReport, projection, *checks)

    source = expand_path(path_value)
    if source.is_symlink() or not source.exists() or not source.is_dir():
        return report(
            skill_check(
                "ERROR",
                f"adopt path must be an existing non-symlink directory: {source}",
                project=project,
            )
        )
    source = source.resolve()
    destination, rejected = skill_destination(
        projection, explicit_name or source.name, project, "adopting a project skill"
    )
    if rejected is not None:
        return report(rejected)
    assert destination is not None
    fields = {"name": destination.name, "project": project, "target": str(destination)}
    if destination.exists() or destination.is_symlink():
        return report(
            skill_check("ERROR", f"repository destination already exists: {destination}", **fields)
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    try:
        source.symlink_to(destination, target_is_directory=True)
    except OSError as exc:
        try:
            shutil.move(str(destination), str(source))
        except OSError:
            pass
        return report(
            skill_check(
                "ERROR", f"could not create replacement symlink at {source}: {exc}", **fields
            )
        )
    return report(
        skill_check("ok", f"adopted {source} -> {destination}", **fields),
        skill_check("ok", "run 'hub apply' to deploy the skill to other agents", **fields),
    )
