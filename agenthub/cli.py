"""Command-line arguments and terminal output for Store operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import core, operations
from .config import ConfigError, repo_option_help, resolve_repo

DESCRIPTION = "Keep Agent Skills and instructions in one Git Store."


def print_report(
    report: core.Report, *, errors_to_stderr: bool = False, quiet: bool = False
) -> int:
    output = sys.stderr if errors_to_stderr else sys.stdout
    for check in report.checks:
        if not quiet or check.level in core.PROBLEM_LEVELS or check.level == "warn":
            print(
                f"[{check.level}] {check.text}" if check.level else check.text,
                file=output,
            )
    return report.exit_code


def _options(parser: argparse.ArgumentParser, *, child: bool = False) -> None:
    default = argparse.SUPPRESS if child else None
    parser.add_argument(
        "--store",
        "--repo",
        dest="store",
        type=Path,
        default=default,
        help=repo_option_help(),
    )
    for flag in ("dry-run", "quiet", "json"):
        parser.add_argument(
            f"--{flag}",
            action="store_true",
            default=argparse.SUPPRESS if child else False,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-hub", description=DESCRIPTION)
    _options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    commands = {}
    for name, help_text in (
        ("init", "create the Store and adopt existing Skills"),
        ("apply", "link Skills and render instructions"),
        ("status", "report filesystem and Git drift"),
        ("sync", "commit, pull, apply, and push"),
        ("add-skill", "create a Skill"),
        ("adopt", "move an existing Skill into the Store"),
    ):
        child = subparsers.add_parser(name, help=help_text)
        _options(child, child=True)
        commands[name] = child
    commands["init"].add_argument("--from", dest="from_url")
    commands["init"].add_argument("--remote")
    commands["init"].add_argument("--yes", action="store_true")
    commands["apply"].add_argument("--copy", action="store_true")
    commands["add-skill"].add_argument("name")
    commands["add-skill"].add_argument("--project")
    commands["adopt"].add_argument("path")
    commands["adopt"].add_argument("--project")
    commands["adopt"].add_argument("--name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.command not in {"apply", "sync"}:
        parser.error("--dry-run is supported only by apply and sync")
    try:
        repo = resolve_repo(args.store, create=args.command == "init")
        store = operations.ContentOperations(repo)
        report: core.Report
        if args.command == "init":
            report = store.init(
                from_url=args.from_url, remote=args.remote, yes=args.yes
            )
        elif args.command == "apply":
            report = store.apply(dry_run=args.dry_run, copy=args.copy)
        elif args.command == "status":
            report = store.status()
        elif args.command == "sync":
            report = store.sync(dry_run=args.dry_run)
        elif args.command == "add-skill":
            report = store.add_skill(args.name, args.project)
        else:
            report = store.adopt(args.path, args.project, args.name)
        if args.json:
            print(json.dumps(report.to_dict()))
            return report.exit_code
        return print_report(
            report, errors_to_stderr=not report.machine_id, quiet=args.quiet
        )
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, operations.RepositoryBusyError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
