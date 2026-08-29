"""Bootstrap the App and one Content repository."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import SAFE_NAME, short_hostname


SERVICE_LABEL = "com.agenthub.web"
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 7337


class SetupError(RuntimeError):
    """An operator-facing setup failure."""


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )


@dataclass(frozen=True)
class ContentSelection:
    path: Path
    created: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup.sh",
        description="Connect agent-hub to a Content repository and verify the local App.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--update",
        action="store_true",
        help="fast-forward the App, refresh its environment, and reload its service",
    )
    mode.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the macOS user service while preserving the App and Content",
    )
    mode.add_argument("--finish-update", action="store_true", help=argparse.SUPPRESS)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--content", type=Path, help="use an existing local Content repository")
    source.add_argument("--content-url", help="clone an existing Content Git repository")
    source.add_argument(
        "--new-content",
        type=Path,
        metavar="PATH",
        help="create a new Content repository from Example Content",
    )
    parser.add_argument(
        "--content-dir",
        type=Path,
        help="clone destination (default: an agent-hub-content sibling of the App)",
    )
    parser.add_argument("--machine", help="existing or new stable Machine ID")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="fail instead of prompting for a missing choice",
    )
    peer = parser.add_mutually_exclusive_group()
    peer.add_argument(
        "--peer-token-file",
        type=Path,
        help="read an existing Peer token from a file and store it outside Git",
    )
    peer.add_argument(
        "--generate-peer-token",
        action="store_true",
        help="create and store a new Peer token outside Git",
    )
    return parser


def require_content(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise SetupError(f"Content directory does not exist: {resolved}")
    if not (resolved / "config" / "hub.toml").is_file():
        raise SetupError(f"{resolved} is not a Content repository: config/hub.toml is missing")
    return resolved


def choose_content(args: argparse.Namespace, app_root: Path) -> ContentSelection:
    if args.content is not None:
        return ContentSelection(require_content(args.content))
    if args.content_url is not None:
        destination = args.content_dir or app_root.parent / "agent-hub-content"
        return ContentSelection(clone_content(args.content_url, destination))
    if args.new_content is not None:
        return ContentSelection(create_content(args.new_content, app_root), created=True)
    if args.non_interactive:
        raise SetupError(
            "choose Content with --content PATH, --content-url URL, or --new-content PATH"
        )
    return choose_content_interactively(app_root)


def choose_content_interactively(app_root: Path) -> ContentSelection:
    print("Content source:")
    print("  1. Use a local Content repository")
    print("  2. Clone a Content repository")
    print("  3. Create new Content from Example Content")
    choice = input("Choose 1, 2, or 3: ").strip()
    if choice == "1":
        return ContentSelection(require_content(Path(input("Content path: ").strip())))
    if choice == "2":
        url = input("Content Git URL: ").strip()
        default = app_root.parent / "agent-hub-content"
        entered = input(f"Clone destination [{default}]: ").strip()
        return ContentSelection(clone_content(url, Path(entered) if entered else default))
    if choice == "3":
        default = app_root.parent / "agent-hub-content"
        entered = input(f"New Content path [{default}]: ").strip()
        return ContentSelection(
            create_content(Path(entered) if entered else default, app_root), created=True
        )
    raise SetupError("invalid Content choice; enter 1, 2, or 3")


def clone_content(url: str, destination: Path) -> Path:
    if not url.strip():
        raise SetupError("Content Git URL cannot be empty")
    target = destination.expanduser().resolve()
    if target.exists():
        raise SetupError(f"refusing to overwrite existing clone destination: {target}")
    result = subprocess.run(
        ["git", "clone", "--", url, str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(result.stderr.strip() or f"git clone failed for {url}")
    return require_content(target)


def create_content(destination: Path, app_root: Path) -> Path:
    target = destination.expanduser().resolve()
    if target.exists():
        raise SetupError(f"refusing to overwrite existing Content destination: {target}")
    example = app_root / "example-content"
    if not example.is_dir():
        raise SetupError(f"Example Content directory is missing: {example}")
    require_git_identity(app_root)
    shutil.copytree(example, target)
    result = subprocess.run(
        ["git", "-C", str(target), "init", "-q", "-b", "main"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(result.stderr.strip() or f"could not initialize {target}")
    return require_content(target)


def git_config(app_root: Path, key: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(app_root), "config", "--get", key],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def require_clean_app(app_root: Path) -> None:
    result = run(
        ["git", "-C", str(app_root), "status", "--porcelain", "--untracked-files=normal"]
    )
    if result.returncode != 0:
        raise SetupError(result.stderr.strip() or f"cannot inspect App repository {app_root}")
    if result.stdout:
        raise SetupError("App repository is dirty; commit or discard its changes before update")


def update_app_repository(app_root: Path) -> None:
    require_clean_app(app_root)
    upstream = run(
        [
            "git",
            "-C",
            str(app_root),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ]
    )
    if upstream.returncode != 0:
        raise SetupError("App repository has no upstream branch")
    upstream_branch = upstream.stdout.strip()
    if not upstream_branch:
        raise SetupError("App repository has no upstream branch")
    fetched = run(["git", "-C", str(app_root), "fetch", "--quiet"])
    if fetched.returncode != 0:
        raise SetupError(fetched.stderr.strip() or "could not fetch the App upstream")
    divergence = run(
        [
            "git",
            "-C",
            str(app_root),
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{upstream_branch}",
        ]
    )
    if divergence.returncode != 0:
        raise SetupError(
            divergence.stderr.strip() or "could not compare the App with its upstream"
        )
    try:
        ahead, behind = (int(value) for value in divergence.stdout.split())
    except (TypeError, ValueError):
        raise SetupError(
            divergence.stderr.strip() or "could not compare the App with its upstream"
        ) from None
    if ahead > 0 and behind > 0:
        raise SetupError("App repository has diverged from its upstream; update refused")
    if ahead > 0:
        raise SetupError(
            "App repository has local commits that update will not touch; update refused"
        )
    pulled = run(["git", "-C", str(app_root), "pull", "--ff-only"])
    if pulled.returncode != 0:
        raise SetupError(pulled.stderr.strip() or "could not fast-forward the App repository")


def require_git_identity(app_root: Path) -> tuple[str, str]:
    name = git_config(app_root, "user.name")
    email = git_config(app_root, "user.email")
    if not name or not email:
        raise SetupError(
            "Git user.name and user.email are required for new Content. Configure them "
            "for this App repository, then run setup again:\n"
            f"git -C {app_root} config user.name \"Your Name\"\n"
            f"git -C {app_root} config user.email \"you@example.com\""
        )
    return name, email


def commit_new_content(content: Path, app_root: Path) -> None:
    name, email = require_git_identity(app_root)
    add = subprocess.run(
        ["git", "-C", str(content), "add", "-A"],
        text=True,
        capture_output=True,
        check=False,
    )
    if add.returncode != 0:
        raise SetupError(add.stderr.strip() or "could not stage new Content")
    commit = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "-C",
            str(content),
            "commit",
            "-q",
            "-m",
            "Create agent-hub Content",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if commit.returncode != 0:
        raise SetupError(commit.stderr.strip() or "could not commit new Content")


def load_machines(path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc
    machines = data.get("machines")
    if not isinstance(machines, dict) or not all(
        isinstance(host, str) and isinstance(machine, str) and machine
        for host, machine in machines.items()
    ):
        raise SetupError(f"{path}: [machines] must map hostnames to Machine IDs")
    return machines


def register_machine(content: Path, requested: str | None, non_interactive: bool) -> str:
    path = content / "config" / "hub.toml"
    machines = load_machines(path)
    hostname = platform.node()
    short = short_hostname(hostname)
    current = machines.get(short) or machines.get(hostname)
    if requested is None and current:
        return current
    if requested is None:
        if non_interactive:
            raise SetupError("this hostname is not registered; pass --machine MACHINE_ID")
        known = sorted(set(machines.values()))
        if known:
            print(f"Existing Machine IDs: {', '.join(known)}")
        requested = input(f"Machine ID [{short.lower()}]: ").strip() or short.lower()
    if SAFE_NAME.fullmatch(requested) is None:
        raise SetupError(
            f"invalid Machine ID {requested!r}; use lowercase ASCII letters and digits "
            "with single '-' or '_' separators"
        )
    if current and current != requested:
        raise SetupError(
            f"hostname {short!r} is already registered as Machine {current!r}; "
            f"remove that mapping before selecting {requested!r}"
        )
    if current:
        return current
    append_machine(path, short, requested)
    return requested


def append_machine(path: Path, hostname: str, machine: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    insertion = len(lines)
    in_machines = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[machines]":
            in_machines = True
            continue
        if in_machines and stripped.startswith("["):
            insertion = index
            break
    if not in_machines:
        suffix = "" if text.endswith("\n") or not text else "\n"
        lines.extend([suffix, "[machines]\n"])
        insertion = len(lines)
    if insertion > 0 and not lines[insertion - 1].endswith(("\n", "\r")):
        lines[insertion - 1] += "\n"
    entry = f"{json.dumps(hostname)} = {json.dumps(machine)}\n"
    lines.insert(insertion, entry)
    path.write_text("".join(lines), encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_pointer(home: Path, content: Path) -> Path:
    pointer = home / ".config" / "agent-hub" / "root"
    atomic_write(pointer, f"{content}\n")
    return pointer


def read_pointer(home: Path) -> tuple[Path, Path]:
    pointer = home / ".config" / "agent-hub" / "root"
    try:
        value = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"cannot read Content pointer {pointer}: {exc}") from exc
    if not value:
        raise SetupError(f"Content pointer is empty: {pointer}")
    return pointer, require_content(Path(value))


def install_app(app_root: Path, python: Path) -> tuple[Path, Path]:
    venv = app_root / ".venv"
    venv_python = venv / "bin" / "python"
    if not venv_python.is_file():
        result = subprocess.run(
            [str(python), "-m", "venv", str(venv)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SetupError(result.stderr.strip() or "could not create the App environment")
    result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-e", str(app_root)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(result.stderr.strip() or "could not install the App environment")
    cli = venv / "bin" / "agent-hub"
    web = venv / "bin" / "agent-hub-web"
    if not cli.is_file() or not web.is_file():
        raise SetupError("the App install did not create agent-hub and agent-hub-web commands")
    return cli, web


def run_status(cli: Path, content: Path, machine: str) -> None:
    environment = dict(os.environ, AGENT_HUB_MACHINE=machine)
    result = subprocess.run(
        [str(cli), "--repo", str(content), "status"],
        text=True,
        env=environment,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode not in {0, 1}:
        raise SetupError(result.stderr.strip() or "agent-hub status failed")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def verify_web(web: Path, content: Path) -> None:
    configured_port = os.environ.get("AGENT_HUB_SETUP_SMOKE_PORT")
    port = int(configured_port) if configured_port else free_port()
    process = subprocess.Popen(
        [
            str(web),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--repo",
            str(content),
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(50):
            if process.poll() is not None:
                error = process.stderr.read().strip() if process.stderr else ""
                raise SetupError(error or "temporary Web process exited before verification")
            if probe_http(url):
                return
            time.sleep(0.1)
        raise SetupError(f"temporary Web process did not return HTTP 200 at {url}")
    finally:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def probe_http(url: str) -> bool:
    command = os.environ.get("AGENT_HUB_SETUP_HTTP_PROBE")
    if command:
        result = subprocess.run(
            [command, url],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout == "200"
    try:
        with urllib.request.urlopen(url, timeout=0.2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def install_macos_service(home: Path, app_root: Path, web: Path) -> Path:
    plist_path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
    log_dir = home / "Library" / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    service = {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            str(web),
            "--host",
            SERVICE_HOST,
            "--port",
            str(SERVICE_PORT),
            "--quiet",
        ],
        "WorkingDirectory": str(app_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {
            "HOME": str(home),
            "AGENT_HUB_REPO": "",
        },
        "StandardOutPath": str(log_dir / "agent-hub-web.log"),
        "StandardErrorPath": str(log_dir / "agent-hub-web.error.log"),
    }
    atomic_write(plist_path, plistlib.dumps(service).decode("utf-8"))
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"],
        text=True,
        capture_output=True,
        check=False,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if loaded.returncode != 0:
        raise SetupError(loaded.stderr.strip() or f"could not load {SERVICE_LABEL}")
    url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/"
    for _ in range(50):
        if probe_http(url):
            return plist_path
        time.sleep(0.1)
    raise SetupError(f"reloaded service did not return HTTP 200 at {url}")


def uninstall_macos_service(home: Path) -> Path:
    plist_path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    loaded = subprocess.run(
        ["launchctl", "print", f"{domain}/{SERVICE_LABEL}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if loaded.returncode == 0:
        removed = subprocess.run(
            ["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if removed.returncode != 0:
            raise SetupError(removed.stderr.strip() or f"could not unload {SERVICE_LABEL}")
    plist_path.unlink(missing_ok=True)
    return plist_path


def store_peer_token(home: Path, value: str) -> Path:
    value = value.strip()
    if not value:
        raise SetupError("Peer token cannot be empty")
    path = home / ".config" / "agent-hub" / "peer-token"
    atomic_write(path, f"{value}\n")
    return path


def read_peer_token(path: Path) -> str:
    source = path.expanduser().resolve()
    try:
        return source.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"cannot read Peer token file {source}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.content_dir is not None and args.content_url is None:
        parser.error("--content-dir requires --content-url")
    app_root = Path(os.environ["AGENT_HUB_SETUP_APP_ROOT"]).resolve()
    python = Path(os.environ["AGENT_HUB_SETUP_PYTHON"]).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    system = os.environ.get("AGENT_HUB_SETUP_PLATFORM", platform.system())
    try:
        if args.uninstall:
            if system == "Darwin":
                uninstall_macos_service(home)
                print(f"[ok] uninstalled {SERVICE_LABEL}")
            else:
                print(f"[ok] no service to remove on {system}")
            return 0
        if args.update:
            read_pointer(home)
            update_app_repository(app_root)
            setup = app_root / "setup.sh"
            if not setup.is_file():
                raise SetupError(f"updated installer is missing: {setup}")
            os.execv("/bin/sh", ["/bin/sh", str(setup), "--finish-update"])
        if args.finish_update:
            pointer, content = read_pointer(home)
            _, web = install_app(app_root, python)
            if system == "Darwin":
                install_macos_service(home, app_root, web)
            else:
                verify_web(web, content)
            foreground = shlex.join(
                [str(web), "--host", SERVICE_HOST, "--port", str(SERVICE_PORT)]
            )
            print(f"[ok] updated App: {app_root}")
            print(f"[ok] Content pointer preserved: {pointer}")
            print("[ok] Web UI returned HTTP 200")
            if system == "Linux":
                print(f"Run the Web UI in the foreground: {foreground}")
            return 0
        selection = choose_content(args, app_root)
        content = selection.path
        machine = register_machine(content, args.machine, args.non_interactive)
        if selection.created:
            commit_new_content(content, app_root)
        pointer = write_pointer(home, content)
        if args.peer_token_file is not None:
            token = store_peer_token(home, read_peer_token(args.peer_token_file))
            print(f"[ok] Peer token: {token}")
        elif args.generate_peer_token:
            token = store_peer_token(home, secrets.token_hex(32))
            print(f"[ok] Peer token: {token}")
        cli, web = install_app(app_root, python)
        run_status(cli, content, machine)
        if system == "Darwin":
            install_macos_service(home, app_root, web)
        else:
            verify_web(web, content)
    except EOFError:
        print("[ERROR] input ended before setup was complete", file=sys.stderr)
        return 1
    except (OSError, SetupError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    dry_run = shlex.join([str(cli), "--repo", str(content), "--dry-run", "apply"])
    foreground = shlex.join([str(web), "--host", "127.0.0.1", "--port", "7337"])
    print(f"[ok] local Content: {content}")
    print(f"[ok] Machine: {machine}")
    print(f"[ok] Content pointer: {pointer}")
    print("[ok] Web UI returned HTTP 200")
    print(f"Review managed-file changes: {dry_run}")
    if system == "Linux":
        print(f"Run the Web UI in the foreground: {foreground}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
