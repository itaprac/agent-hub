"""Command-line arguments and terminal output for Store operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, core, operations
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    commands = {}
    for name, help_text in (
        ("init", "create the Store and adopt existing Skills"),
        ("migrate", "convert a v1 repository to the Store layout"),
        ("project", "link private Project skills"),
        ("apply", "link Skills and render instructions"),
        ("status", "report filesystem and Git drift"),
        ("sync", "commit, pull, apply, and push"),
        ("install", "install Skills through skills.sh"),
        ("update", "update Installed skills through skills.sh"),
        ("add-skill", "create a Skill"),
        ("adopt", "move an existing Skill into the Store"),
        ("timer", "control automatic synchronization"),
        ("ui", "run the Console"),
        ("remote", "pair this Machine for remote Console actions"),
    ):
        child = subparsers.add_parser(name, help=help_text)
        _options(child, child=True)
        commands[name] = child
    commands["init"].add_argument("--from", dest="from_url")
    commands["init"].add_argument("--remote")
    commands["init"].add_argument("--yes", action="store_true")
    commands["apply"].add_argument("--copy", action="store_true")
    commands["sync"].add_argument("--prefer", choices=("local", "remote"))
    commands["status"].add_argument("--fleet", action="store_true")
    commands["install"].add_argument("source")
    commands["install"].add_argument("--skill")
    commands["update"].add_argument("names", nargs="*")
    commands["add-skill"].add_argument("name")
    commands["add-skill"].add_argument("--project")
    commands["adopt"].add_argument("path")
    commands["adopt"].add_argument("--project", action="store_true")
    commands["adopt"].add_argument("--name")
    commands["migrate"].add_argument("path", type=Path)
    commands["timer"].add_argument("action", choices=("on", "off", "status"))
    commands["ui"].add_argument("--port", type=int, default=7337)
    commands["ui"].add_argument("--host", default="127.0.0.1")
    commands["ui"].add_argument("--service", choices=("on", "off", "status"))
    remote_commands = commands["remote"].add_subparsers(dest="remote_command", required=True)
    trust = remote_commands.add_parser("trust", help="allow a controller key to run Status, Apply, and Sync")
    _options(trust, child=True)
    trust.add_argument("--public-key", required=True)
    trust.add_argument("--controller", required=True, help="controller Tailscale IPv4 address")
    trust.add_argument("--executable", type=Path, default=Path(sys.argv[0]).absolute())
    project_commands = commands["project"].add_subparsers(
        dest="project_command", required=True
    )
    link = project_commands.add_parser(
        "link", help="record a project and link its Skills"
    )
    _options(link, child=True)
    link.add_argument("path", nargs="?", default=".", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.command not in {"apply", "sync"}:
        parser.error("--dry-run is supported only by apply and sync")
    if args.command == "ui" and args.service is not None:
        if args.host != "127.0.0.1" or args.port != 7337:
            parser.error("--service uses host 127.0.0.1 and port 7337")
    if args.command == "ui" and args.service is None and args.json:
        parser.error("--json requires a service command")
    try:
        repo = resolve_repo(
            args.path if args.command == "migrate" else args.store,
            create=args.command == "init"
            or (args.command == "timer" and args.action in {"off", "status"})
            or (args.command == "ui" and args.service in {"off", "status"}),
        )
        if args.command == "ui" and args.service is None:
            from . import webapp

            ui_args = ["--store", str(repo), "--host", args.host, "--port", str(args.port)]
            if args.quiet:
                ui_args.append("--quiet")
            return webapp.main(ui_args)
        store = operations.ContentOperations(repo)
        report: core.Report
        if args.command in {"timer", "ui"}:
            from . import services

            report = (
                services.timer(args.action, repo)
                if args.command == "timer"
                else services.ui_service(args.service, repo)
            )
        elif args.command == "migrate":
            report = store.migrate()
        elif args.command == "remote":
            from . import pairing

            report = pairing.trust(args.public_key, args.controller, repo, args.executable)
        elif args.command == "project":
            report = store.project_link(args.path)
        elif args.command == "init":
            report = store.init(
                from_url=args.from_url, remote=args.remote, yes=args.yes
            )
        elif args.command == "apply":
            report = store.apply(dry_run=args.dry_run, copy=args.copy)
        elif args.command == "status":
            report = store.status(fleet=args.fleet)
        elif args.command == "sync":
            report = store.sync(dry_run=args.dry_run, prefer=args.prefer)
        elif args.command == "install":
            report = store.install(args.source, args.skill)
        elif args.command == "update":
            report = store.update(args.names)
        elif args.command == "add-skill":
            report = store.add_skill(args.name, args.project)
        elif args.command == "adopt":
            report = store.adopt(args.path, args.project, args.name)
        else:
            parser.error(f"unsupported command: {args.command}")
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
