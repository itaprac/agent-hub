#!/usr/bin/env python3
"""Local web UI for agent-hub: status dashboard, apply/sync and repository editing."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config as hub_config
from . import files as content_files
from . import gitio
from . import operations
from . import skills as installed_skills
from . import usage

MAX_BODY_BYTES = content_files.MAX_FILE_BYTES + 64 * 1024
RUN_COMMANDS = frozenset({"apply", "sync", "install", "update"})

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
INSTALLED_WEB_ROOT = Path(__file__).resolve().parent / "web_assets"
WEB_ROOT = INSTALLED_WEB_ROOT if (INSTALLED_WEB_ROOT / "index.html").is_file() else SOURCE_WEB_ROOT


class ApiError(Exception):
    """An error that is safe to return to the browser."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- hub


def run_command(
    content_operations: operations.ContentOperations,
    command: str,
    dry_run: bool,
    prefer: str | None = None,
    source: str | None = None,
    skill: str | None = None,
    names: list[str] | None = None,
) -> dict[str, Any]:
    if command == "apply":
        return content_operations.apply(dry_run=dry_run).to_dict()
    if command == "sync":
        return content_operations.sync(dry_run=dry_run, prefer=prefer).to_dict()
    if command == "install" and source is not None:
        return content_operations.install(source, skill).to_dict()
    if command == "update":
        return content_operations.update(names).to_dict()
    raise ApiError(400, f"unknown command: {command}")


def git_state(repo: Path, fetch: bool = True) -> dict[str, Any]:
    try:
        return operations.ContentOperations(repo).git(fetch=fetch)
    except gitio.GitCommandError as exc:
        status = 504 if "timed out" in str(exc) else 500
        raise ApiError(status, str(exc)) from exc
    except gitio.GitError as exc:
        raise ApiError(500, str(exc)) from exc


# --------------------------------------------------------------------------- files

expected_revision = content_files.expected_revision


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
    route("GET", r"/api/fleet", "get_fleet"),
    route("GET", r"/api/status", "get_status"),
    route("GET", r"/api/usage", "get_usage"),
    route("GET", r"/api/usage/settings", "get_usage_settings"),
    route("PUT", r"/api/usage/settings", "put_usage_settings"),
    route("GET", r"/api/file", "get_file"),
    route("POST", r"/api/run", "post_run"),
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
        self.send_payload(
            status, body, "application/json; charset=utf-8", headers=headers
        )

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
        return (
            parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()
        )

    def authorize_mutation(self, route: str) -> None:
        if self.is_same_origin_browser_request():
            return
        # The request body has not been consumed yet. Closing prevents a reverse
        # proxy from reusing this HTTP/1.1 connection with unread bytes on it.
        self.close_connection = True
        raise ApiError(401, "a same-origin browser request is required")

    def dispatch(self, method: str) -> None:
        try:
            self.handle_route(method)
        except ApiError as exc:
            self.send_error_json(exc.status, exc.message)
        except content_files.FileError as exc:
            self.send_error_json(exc.status, exc.message)
        except (hub_config.ConfigError, OSError, UnicodeError) as exc:
            self.send_error_json(500, str(exc))
        except operations.RepositoryBusyError as exc:
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
        self.send_json(operations.ContentOperations(self.repo).state())

    def get_git(self, _match: re.Match[str]) -> None:
        fetch = (self.query().get("fetch") or ["1"])[0] != "0"
        self.send_json(git_state(self.repo, fetch=fetch))

    def get_fleet(self, _match: re.Match[str]) -> None:
        self.send_json(operations.ContentOperations(self.repo).fleet())

    def get_status(self, _match: re.Match[str]) -> None:
        self.send_json(operations.ContentOperations(self.repo).status().to_dict())

    def get_usage(self, _match: re.Match[str]) -> None:
        query = self.query()
        try:
            days = int((query.get("days") or ["30"])[0])
        except ValueError:
            days = 30
        time_zone = query["tz"][0] if query.get("tz") else None
        self.send_json(usage.read_summary(days=days, time_zone=time_zone))

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
        file = operations.ContentOperations(self.repo).read_file(
            values[0] if values else None
        )
        self.send_json(file, headers={"ETag": f'"{file["revision"]}"'})

    def _command_payload(self) -> dict[str, Any]:
        payload = self.read_json()
        command = payload.get("command")
        if not isinstance(command, str) or command not in RUN_COMMANDS:
            raise ApiError(
                400, f"command must be one of: {', '.join(sorted(RUN_COMMANDS))}"
            )
        dry_run = payload.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ApiError(400, "dry_run must be a boolean")
        prefer = payload.get("prefer")
        if prefer is not None and (
            command != "sync" or prefer not in ("local", "remote")
        ):
            raise ApiError(
                400, "prefer is valid only for sync and must be local or remote"
            )
        if dry_run and command not in {"apply", "sync"}:
            raise ApiError(400, "dry_run is supported only by apply and sync")
        options: dict[str, Any] = {"command": command, "dry_run": dry_run, "prefer": prefer}
        try:
            if command == "install":
                options["source"] = installed_skills.source_value(payload.get("source"))
                if "skill" in payload and payload["skill"] is not None:
                    options["skill"] = installed_skills.skill_names([payload["skill"]])[0]
            if command == "update":
                options["names"] = installed_skills.skill_names(payload.get("names", []))
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        return options

    def post_run(self, _match: re.Match[str]) -> None:
        options = self._command_payload()
        self.send_json(
            run_command(
                operations.ContentOperations(self.repo), **options
            )
        )

    def post_add_skill(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        report = operations.ContentOperations(self.repo).add_skill(
            required_name(payload, "name"), optional_name(payload, "project")
        )
        self.send_json(report.to_dict())

    def post_adopt(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        project = payload.get("project", False)
        if not isinstance(project, bool):
            raise ApiError(400, "project must be a boolean")
        report = operations.ContentOperations(self.repo).adopt(
            required_name(payload, "path"),
            project,
            optional_name(payload, "name"),
        )
        self.send_json(report.to_dict())

    def put_file(self, _match: re.Match[str]) -> None:
        payload = self.read_json()
        file = operations.ContentOperations(self.repo).write_file(
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
                raise ApiError(
                    428, "revision is required; reload the file before changing it"
                )
            revision = expected_revision({"revision": revisions[0]})
        self.send_json(
            operations.ContentOperations(self.repo).delete_file(value, revision)
        )

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
            self.send_payload(
                500, f"cannot read asset: {exc}\n".encode(), "text/plain; charset=utf-8"
            )
            return
        content_type = STATIC_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        self.send_payload(200, body, content_type)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=7337, help="TCP port (0 picks a free one)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--store",
        "--repo",
        dest="repo",
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
        print(
            "[skip] no authentication: expose this only on a trusted private network",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
