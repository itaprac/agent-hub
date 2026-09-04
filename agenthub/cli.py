"""Command-line front end: argument parsing and terminal formatting."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, operations
from .config import ConfigError, repo_option_help, resolve_repo

DESCRIPTION = "Deploy agent skills and instructions from a single git repository."


def print_report(report: core.Report, *, errors_to_stderr: bool = False) -> int:
    output = sys.stderr if errors_to_stderr else sys.stdout
    for check in report.checks:
        print(f"[{check.level}] {check.text}" if check.level else check.text, file=output)
    return report.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-hub", description=DESCRIPTION)
    parser.add_argument("--repo", type=Path, default=None, help=repo_option_help())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show actions without changing files (apply and sync only)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("apply", help="deploy the declared state")
    subparsers.add_parser("status", help="report filesystem and git drift")
    subparsers.add_parser("sync", help="commit, pull, apply, and push")

    add_parser = subparsers.add_parser("add-skill", help="create a skill skeleton")
    add_parser.add_argument("name")
    add_parser.add_argument("--project")

    adopt_parser = subparsers.add_parser("adopt", help="move an existing skill into the hub")
    adopt_parser.add_argument("path")
    adopt_parser.add_argument("--project")
    adopt_parser.add_argument("--name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.command not in {"apply", "sync"}:
        parser.error("--dry-run is supported only by apply and sync")
    try:
        repo = resolve_repo(args.repo)
        content_operations = operations.ContentOperations(repo)
        report: core.Report
        if args.command == "apply":
            report = content_operations.apply(dry_run=args.dry_run)
        if args.command == "status":
            report = content_operations.status()
        if args.command == "sync":
            report = content_operations.sync(dry_run=args.dry_run)
        if args.command == "add-skill":
            report = content_operations.add_skill(args.name, args.project)
        if args.command == "adopt":
            report = content_operations.adopt(args.path, args.project, args.name)
        return print_report(report, errors_to_stderr=not report.machine_id)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
