#!/usr/bin/env python3
"""Local web UI for agent-hub: status dashboard, apply/sync and repository editing."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import re
import sys
import traceback
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config as hub_config
from . import core as hub_core
from . import files as content_files
from . import gitio
from . import peers
from . import repository
from . import usage

TEXT_SUFFIXES = content_files.TEXT_SUFFIXES
MAX_FILE_BYTES = content_files.MAX_FILE_BYTES
MAX_BODY_BYTES = content_files.MAX_FILE_BYTES + 64 * 1024
RUN_COMMANDS = frozenset({"apply", "sync"})

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

APP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_WEB_ROOT = APP_ROOT / "web"
INSTALLED_WEB_ROOT = Path(sys.prefix) / "share" / "agent-hub" / "web"
WEB_ROOT = SOURCE_WEB_ROOT if SOURCE_WEB_ROOT.is_dir() else INSTALLED_WEB_ROOT


class ApiError(Exception):
    """An error that is safe to return to the browser."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@contextlib.contextmanager
def repository_operation() -> Iterator[None]:
    """Map package-level repository contention to the HTTP error contract."""
    try:
        with repository.mutation():
            yield
    except repository.RepositoryBusyError as exc:
        raise ApiError(423, str(exc)) from exc


# --------------------------------------------------------------------------- hub

def require_projection(repo: Path) -> hub_config.MachineProjection:
    try:
        return hub_config.load_machine_projection(repo)
    except hub_config.ConfigError as exc:
        raise ApiError(500, str(exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise ApiError(500, str(exc)) from exc


def status_result(
    repo: Path, projection: hub_config.MachineProjection | None = None
) -> dict[str, Any]:
    """Report status from the shared package instead of parsing CLI output."""
    try:
        with repository_operation():
            report = hub_core.status_report(
                projection
                if projection is not None
                else hub_config.load_machine_projection(repo)
            )
    except hub_config.ConfigError as exc:
        # The dashboard shows this inline, exactly as the parsed CLI error did.
        report = hub_core.config_error_report(repo, str(exc))
    except (OSError, UnicodeError) as exc:
        raise ApiError(500, str(exc)) from exc
    return report.to_dict()


def apply_result(
    repo: Path,
    dry_run: bool,
    projection: hub_config.MachineProjection | None = None,
) -> dict[str, Any]:
    """Apply from the shared package instead of parsing CLI output."""
    try:
        with repository_operation():
            report = hub_core.apply_report(
                projection
                if projection is not None
                else hub_config.load_machine_projection(repo),
                dry_run=dry_run,
            )
    except hub_config.ConfigError as exc:
        # The CLI reports an unreadable fleet configuration with one error, exit 2.
        report = hub_core.apply_error_report(
            repo, str(exc), kind="config", exit_code=2, dry_run=dry_run
        )
    except (OSError, UnicodeError) as exc:
        # Keep filesystem failures in the command report instead of returning 500.
        report = hub_core.apply_error_report(
            repo, str(exc), kind="error", exit_code=1, dry_run=dry_run
        )
    return report.to_dict()


def sync_result(
    repo: Path,
    dry_run: bool,
    projection: hub_config.MachineProjection | None = None,
) -> dict[str, Any]:
    """Sync from the shared package instead of parsing CLI output."""
    try:
        with repository_operation():
            report = hub_core.sync_report(
                projection
                if projection is not None
                else hub_config.load_machine_projection(repo),
                dry_run=dry_run,
            )
    except hub_config.ConfigError as exc:
        # The CLI reports an unreadable fleet configuration with one error, exit 2.
        report = hub_core.sync_error_report(
            repo, str(exc), kind="config", exit_code=2, dry_run=dry_run
        )
    except (OSError, UnicodeError) as exc:
        # Keep filesystem failures in the command report instead of returning 500.
        report = hub_core.sync_error_report(
            repo, str(exc), kind="error", exit_code=1, dry_run=dry_run
        )
    return report.to_dict()


def add_skill_result(repo: Path, name: str, project: str | None) -> dict[str, Any]:
    """Create a skill through the shared package instead of parsing CLI output."""
    try:
        with repository_operation():
            report = hub_core.add_skill_report(
                hub_config.load_machine_projection(repo), name, project
            )
    except hub_config.ConfigError as exc:
        # The CLI reports an unreadable fleet configuration with one error, exit 2.
        report = hub_core.add_skill_error_report(repo, str(exc), kind="config", exit_code=2)
    except (OSError, UnicodeError) as exc:
        report = hub_core.add_skill_error_report(repo, str(exc), kind="error", exit_code=1)
    return report.to_dict()


def adopt_result(
    repo: Path, path: str, project: str | None, name: str | None
) -> dict[str, Any]:
    """Adopt a skill through the shared package instead of parsing CLI output."""
    try:
        with repository_operation():
            report = hub_core.adopt_skill_report(
                hub_config.load_machine_projection(repo), path, project, name
            )
    except hub_config.ConfigError as exc:
        # The CLI reports an unreadable fleet configuration with one error, exit 2.
        report = hub_core.adopt_error_report(repo, str(exc), kind="config", exit_code=2)
    except (OSError, UnicodeError) as exc:
        report = hub_core.adopt_error_report(repo, str(exc), kind="error", exit_code=1)
    return report.to_dict()


def run_command(
    repo: Path,
    command: str,
    dry_run: bool,
    projection: hub_config.MachineProjection | None = None,
) -> dict[str, Any]:
    """Run one operator command through the shared package."""
    if command == "apply":
        return apply_result(repo, dry_run, projection)
    if command == "sync":
        return sync_result(repo, dry_run, projection)
    raise ApiError(400, f"unknown command: {command}")


class _WebLocalMachine:
    """Adapt local package operations to the Peer federation seam."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo

    def git(self, *, fetch: bool) -> dict[str, Any]:
        return git_state(self._repo, fetch=fetch)

    def status(
        self, projection: hub_config.MachineProjection
    ) -> dict[str, Any]:
        return status_result(self._repo, projection)

    def usage(self, *, days: int, time_zone: str | None) -> dict[str, Any]:
        return usage.read_summary(days=days, time_zone=time_zone)

    def run(
        self,
        projection: hub_config.MachineProjection,
        *,
        command: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        return run_command(self._repo, command, dry_run, projection)


# --------------------------------------------------------------------------- peers/git


def git_state(repo: Path, fetch: bool = True) -> dict[str, Any]:
    try:
        return gitio.state(repo, fetch=fetch)
    except gitio.GitCommandError as exc:
        status = 504 if "timed out" in str(exc) else 500
        raise ApiError(status, str(exc)) from exc
    except gitio.GitError as exc:
        raise ApiError(500, str(exc)) from exc


# --------------------------------------------------------------------------- state

def repo_relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def list_skills(parent: Path, repo: Path) -> list[dict[str, Any]]:
    skills = []
    # The package owns the canonical skill directory rule (CONTEXT.md).
    for child in hub_config.skill_directories(parent):
        files = []
        for path in sorted(child.rglob("*"), key=lambda item: str(item).lower()):
            if not path.is_file() or any(
                part.startswith(".") for part in path.relative_to(child).parts
            ):
                continue
            files.append(
                {
                    "name": str(path.relative_to(child)),
                    "path": repo_relative(path, repo),
                    "size": path.stat().st_size,
                    "editable": path.suffix.lower() in TEXT_SUFFIXES,
                }
            )
        skills.append({"name": child.name, "path": repo_relative(child, repo), "files": files})
    return skills


def list_instructions(directory: Path, repo: Path, agents: list[str]) -> list[dict[str, Any]]:
    names = ["base.md"] + [f"{agent}.md" for agent in agents]
    if directory.is_dir():
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name.lower()):
            if path.name not in names:
                names.append(path.name)
    entries = []
    for name in names:
        path = directory / name
        stem = name[:-3]
        kind = "base" if name == "base.md" else ("agent" if stem in agents else "extra")
        entries.append(
            {
                "name": name,
                "path": repo_relative(path, repo),
                "exists": path.is_file(),
                "kind": kind,
            }
        )
    return entries


def build_state(repo: Path) -> dict[str, Any]:
    projection = require_projection(repo)
    repo = projection.repo
    machine_id = projection.machine_id

    agents = [
        {
            "name": agent.name,
            "mode": agent.mode,
            "keys": dict(agent.target_templates),
        }
        for agent in projection.agents
    ]

    projects = [
        {
            "name": project.name,
            "path": str(project.path) if project.path is not None else None,
            "machines": dict(project.machines),
            "available": project.available,
            "note": (
                ""
                if project.available
                else project.reason
                if project.availability == "no_path"
                else "path does not exist on this machine"
            ),
        }
        for project in projection.projects
    ]

    skills_root = repo / "skills"
    instructions_root = repo / "instructions"
    project_names = [project["name"] for project in projects]

    # Only agents that declare an instructions target can use an overlay file.
    global_agents = [agent.name for agent in projection.agents if agent.instructions_global]
    project_agents = [agent.name for agent in projection.agents if agent.instructions_project]

    config_dir = repo / "config"
    known_configs = ["hub.toml", "agents.toml", "projects.toml", "skills.toml"]
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.toml"), key=lambda item: item.name.lower()):
            if path.name not in known_configs:
                known_configs.append(path.name)

    return {
        "machine_id": machine_id,
        "hostname": projection.hostname,
        "repo": str(repo),
        "agents": agents,
        "projects": projects,
        "skills": {
            "global": list_skills(skills_root / "global", repo),
            "projects": {
                name: list_skills(skills_root / "projects" / name, repo) for name in project_names
            },
        },
        "instructions": {
            "global": list_instructions(instructions_root / "global", repo, global_agents),
            "projects": {
                name: list_instructions(instructions_root / "projects" / name, repo, project_agents)
                for name in project_names
            },
        },
        "config_files": [
            {
                "name": name,
                "path": repo_relative(config_dir / name, repo),
                "exists": (config_dir / name).is_file(),
            }
            for name in known_configs
        ],
        "text_suffixes": sorted(TEXT_SUFFIXES),
        "max_file_bytes": MAX_FILE_BYTES,
    }


# --------------------------------------------------------------------------- files

resolve_repo_file = content_files.resolve
read_repo_file = content_files.read
expected_revision = content_files.expected_revision


write_repo_file = content_files.write
delete_repo_file = content_files.delete


def optional_name(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ApiError(400, f"{key} must be a string")
    value = value.strip()
    if value.startswith("-"):
        raise ApiError(400, f"{key} must not start with '-'")
    return value


def required_name(payload: dict[str, Any], key: str) -> str:
    value = optional_name(payload, key)
    if value is None:
        raise ApiError(400, f"{key} is required")
    return value


# --------------------------------------------------------------------------- http

@dataclasses.dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: str


def route(method: str, pattern: str, handler: str) -> Route:
    return Route(method, re.compile(pattern), handler)


ROUTES = (
    route("GET", r"/api/state", "get_state"),
    route("GET", r"/api/git", "get_git"),
    route("GET", r"/api/peers", "get_peers"),
    route("GET", r"/api/status", "get_status"),
    route("GET", r"/api/usage", "get_usage"),
    route("GET", r"/api/usage/settings", "get_usage_settings"),
    route("PUT", r"/api/usage/settings", "put_usage_settings"),
    route("GET", r"/api/file", "get_file"),
    route("POST", r"/api/run", "post_run"),
    route("POST", r"/api/peers/(?P<machine>[^/]+)/run", "post_peer_run"),
    route("POST", r"/api/add-skill", "post_add_skill"),
    route("POST", r"/api/adopt", "post_adopt"),
    route("PUT", r"/api/file", "put_file"),
    route("DELETE", r"/api/file", "delete_file"),
)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agent-hub-web"
    sys_version = ""

    repo: Path = APP_ROOT
    quiet: bool = False

    # -- helpers

    def log_message(self, format: str, *args: Any) -> None:
        if not self.quiet:
            sys.stderr.write(f"{self.address_string()} {format % args}\n")

    def peer_federation(self) -> peers.PeerFederation:
        return peers.PeerFederation(
            self.repo, _WebLocalMachine(self.repo), peers.HttpPeerTransport()
        )

    def send_payload(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; connect-src 'self'; "
            "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' data:; object-src 'none'; script-src 'self'; style-src 'self'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(
        self,
        payload: Any,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=None).encode("utf-8")
        self.send_payload(status, body, "application/json; charset=utf-8", headers=headers)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "invalid Content-Length header") from None
        if length <= 0:
            raise ApiError(400, "a JSON body is required")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, f"request body is larger than {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, f"invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "the JSON body must be an object")
        return payload

    def query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def route(self) -> str:
        return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)

    def is_same_origin_browser_request(self) -> bool:
        if self.headers.get("Sec-Fetch-Site", "").lower() == "same-origin":
            return True
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if not origin or not host:
            return False
        parsed = urllib.parse.urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()

    def authorize_mutation(self, route: str) -> None:
        # The browser is authorized by the private-network boundary and protected
        # from cross-site requests here. The shared secret is server-to-server only.
        if self.is_same_origin_browser_request():
            return
        supplied = self.headers.get("X-Hub-Token", "")
        if route == "/api/run" and self.peer_federation().authorizes(supplied):
            return
        # The request body has not been consumed yet. Closing prevents a reverse
        # proxy from reusing this HTTP/1.1 connection with unread bytes on it.
        self.close_connection = True
        raise ApiError(401, "authentication required")

    def dispatch(self, method: str) -> None:
        try:
            self.handle_route(method)
        except ApiError as exc:
            self.send_error_json(exc.status, exc.message)
        except peers.PeerError as exc:
            self.send_error_json(exc.status, exc.message)
        except content_files.FileError as exc:
            self.send_error_json(exc.status, exc.message)
        except repository.RepositoryBusyError as exc:
            self.send_error_json(423, str(exc))
        except BrokenPipeError:
            raise
        except Exception:  # keep the server alive on unexpected failures
            sys.stderr.write(f"unhandled error during {method} {self.path}\n")
            traceback.print_exc(file=sys.stderr)
            self.send_error_json(500, "internal server error")

    # -- verbs

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self.dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self.dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch("DELETE")

    # -- routing

    def handle_route(self, method: str) -> None:
        path = self.route()
        if not path.startswith("/api/"):
            if method != "GET":
                raise ApiError(405, f"{method} is not allowed on {path}")
            self.serve_static(path)
            return

        if method in {"POST", "PUT", "DELETE"}:
            self.authorize_mutation(path)

        for item in ROUTES:
            match = item.pattern.fullmatch(path)
            if item.method == method and match is not None:
                handler = getattr(self, item.handler)
                handler(match)
                return
        if any(item.pattern.fullmatch(path) is not None for item in ROUTES):
            raise ApiError(405, f"{method} is not allowed on {path}")
        raise ApiError(404, f"unknown endpoint: {path}")

    def get_state(self, _match: re.Match[str]) -> None:
        self.send_json(build_state(self.repo))

    def get_git(self, _match: re.Match[str]) -> None:
        fetch = (self.query().get("fetch") or ["1"])[0] != "0"
        self.send_json(git_state(self.repo, fetch=fetch))

    def get_peers(self, _match: re.Match[str]) -> None:
        self.send_json(self.peer_federation().state())

    def get_status(self, _match: re.Match[str]) -> None:
        self.send_json(status_result(self.repo))

    def get_usage(self, _match: re.Match[str]) -> None:
        query = self.query()
        try:
            days = int((query.get("days") or ["30"])[0])
        except ValueError:
            days = 30
        time_zone = (query.get("tz") or [None])[0]
        local_only = (query.get("local") or ["0"])[0] == "1"
        self.send_json(
            self.peer_federation().usage(
                days=days, time_zone=time_zone, local_only=local_only
            )
        )

    def get_usage_settings(self, _match: re.Match[str]) -> None:
        self.send_json(usage.public_settings())

    def put_usage_settings(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        allowed = {"claude", "codex", "grok", "cursor", "cursorToken"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError(400, f"unknown usage setting: {', '.join(unknown)}")
        try:
            saved = usage.save_settings(payload)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(500, str(exc)) from exc
        except OSError as exc:
            raise ApiError(500, f"cannot save usage settings: {exc}") from exc
        self.send_json(usage.public_settings(saved))

    def get_file(self, _match: re.Match[str]) -> None:
        values = self.query().get("path") or []
        file = read_repo_file(self.repo, values[0] if values else None)
        self.send_json(file, headers={"ETag": f'"{file["revision"]}"'})

    def _command_payload(self) -> tuple[str, bool]:
        payload = self.read_json()
        command = payload.get("command")
        if command not in RUN_COMMANDS:
            raise ApiError(400, f"command must be one of: {', '.join(sorted(RUN_COMMANDS))}")
        return command, bool(payload.get("dry_run"))

    def post_run(self, _match: re.Match[str]) -> None:
        command, dry_run = self._command_payload()
        self.send_json(run_command(self.repo, command, dry_run))

    def post_peer_run(self, match: re.Match[str]) -> None:
        machine = match.group("machine")
        command, dry_run = self._command_payload()
        self.send_json(
            self.peer_federation().run(
                machine, command=command, dry_run=dry_run
            )
        )

    def post_add_skill(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        self.send_json(
            add_skill_result(
                self.repo, required_name(payload, "name"), optional_name(payload, "project")
            )
        )

    def post_adopt(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        self.send_json(
            adopt_result(
                self.repo,
                required_name(payload, "path"),
                optional_name(payload, "project"),
                optional_name(payload, "name"),
            )
        )

    def put_file(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        file = write_repo_file(
            self.repo,
            payload.get("path"),
            payload.get("content"),
            expected_revision(payload),
        )
        self.send_json(file, headers={"ETag": f'"{file["revision"]}"'})

    def delete_file(self, _match: re.Match[str]) -> None:
        if int(self.headers.get("Content-Length") or 0) > 0:
            payload = self.read_json()
            value = payload.get("path")
            revision = expected_revision(payload)
        else:
            values = self.query().get("path") or []
            value = values[0] if values else None
            revisions = self.query().get("revision") or []
            if not revisions:
                raise ApiError(428, "revision is required; reload the file before changing it")
            revision = expected_revision({"revision": revisions[0]})
        self.send_json(delete_repo_file(self.repo, value, revision))

    def serve_static(self, route: str) -> None:
        relative = route.lstrip("/") or "index.html"
        candidate = (WEB_ROOT / relative).resolve()
        if candidate != WEB_ROOT and WEB_ROOT not in candidate.parents:
            self.send_payload(404, b"not found\n", "text/plain; charset=utf-8")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self.send_payload(404, b"not found\n", "text/plain; charset=utf-8")
            return
        try:
            body = candidate.read_bytes()
        except OSError as exc:
            self.send_payload(500, f"cannot read asset: {exc}\n".encode(), "text/plain; charset=utf-8")
            return
        content_type = STATIC_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        self.send_payload(200, body, content_type)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7337, help="TCP port (0 picks a free one)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help=hub_config.repo_option_help(),
    )
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo = hub_config.resolve_repo(args.repo)
    except hub_config.ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    if not (repo / "config" / "hub.toml").is_file():
        print(f"[ERROR] {repo}: not a content repository; config/hub.toml is missing", file=sys.stderr)
        return 2
    if not WEB_ROOT.is_dir():
        print(
            f"[ERROR] {WEB_ROOT}: web assets directory is missing; install the app "
            "repository in editable mode with 'pip install -e .'",
            file=sys.stderr,
        )
        return 2

    Handler.repo = repo
    Handler.quiet = args.quiet

    try:
        server = Server((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[ERROR] cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    port = server.server_address[1]
    display = "127.0.0.1" if args.host in {"", "0.0.0.0", "::"} else args.host
    print(f"[ok] serving http://{display}:{port} (repo: {repo})", flush=True)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("[skip] no authentication: expose this only on a trusted private network", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
