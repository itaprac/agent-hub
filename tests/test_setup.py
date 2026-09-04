"""Black-box coverage for the five-minute setup path."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from conftest import ROOT, git


SETUP = ROOT / "setup.sh"


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def fake_setup_tools(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    if tools.exists():
        return tools
    tools.mkdir()
    (tools / "bash").symlink_to("/bin/bash")
    real_git = shutil.which("git") or "/usr/bin/git"
    write_executable(
        tools / "git",
        f"""#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$HOME/git.log"
case " $* " in
    *" rev-list "*)
        if [ -n "${{AGENT_HUB_TEST_GIT_REV_LIST_ERROR:-}}" ]; then
            printf '%s\n' "$AGENT_HUB_TEST_GIT_REV_LIST_ERROR" >&2
            exit 2
        fi
        ;;
esac
exec {real_git} "$@"
""",
    )
    for command in ("cat", "chmod", "mkdir", "rm"):
        (tools / command).symlink_to(shutil.which(command) or f"/bin/{command}")
    write_executable(
        tools / "uname",
        "#!/bin/sh\nprintf '%s\\n' \"${AGENT_HUB_TEST_UNAME:-Linux}\"\n",
    )
    write_executable(
        tools / "launchctl",
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$HOME/launchctl.log"
state="$HOME/launchctl.loaded"
case "$1" in
    bootout) rm -f "$state" ;;
    bootstrap)
        if [ -n "${AGENT_HUB_TEST_LAUNCHCTL_BOOTSTRAP_ERROR:-}" ]; then
            printf '%s\n' "$AGENT_HUB_TEST_LAUNCHCTL_BOOTSTRAP_ERROR" >&2
            exit 2
        fi
        if [ -n "${AGENT_HUB_TEST_SERVICE_STDERR:-}" ]; then
            mkdir -p "$HOME/Library/Logs"
            printf '%s\n' "$AGENT_HUB_TEST_SERVICE_STDERR" \
                >> "$HOME/Library/Logs/agent-hub-web.error.log"
        fi
        : > "$state"
        ;;
    print) test -f "$state" ;;
esac
""",
    )
    write_executable(
        tools / "curl",
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$HOME/curl.log"
[ -z "${AGENT_HUB_TEST_WEB_FAIL:-}" ] || exit 1
printf '200'
""",
    )
    write_executable(
        tools / "python3",
        f"""#!/bin/bash
set -eu
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
    bin="$3/bin"
    mkdir -p "$bin"
    cat >"$bin/python" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$HOME/venv-python.log"
exit 0
EOF
    cat >"$bin/agent-hub" <<'EOF'
#!/bin/sh
echo "[MISSING] setup smoke status"
exit "${{AGENT_HUB_TEST_STATUS_EXIT:-1}}"
EOF
    cat >"$bin/agent-hub-web" <<'EOF'
#!{sys.executable}
import argparse
import os
import sys
import time
from pathlib import Path

if os.environ.get("AGENT_HUB_TEST_WEB_FAIL"):
    print("forced Web failure", file=sys.stderr)
    raise SystemExit(2)

parser = argparse.ArgumentParser()
parser.add_argument("--host")
parser.add_argument("--port", type=int)
parser.add_argument("--repo")
parser.add_argument("--quiet", action="store_true")
args = parser.parse_args()

if args.repo is None:
    pointer = Path.home() / ".config" / "agent-hub" / "root"
    try:
        repo = pointer.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"cannot read Content pointer {{pointer}}: {{exc}}", file=sys.stderr)
        raise SystemExit(2)
    if not repo:
        print(f"Content pointer is empty: {{pointer}}", file=sys.stderr)
        raise SystemExit(2)

while True:
    time.sleep(1)
EOF
    chmod +x "$bin/python" "$bin/agent-hub" "$bin/agent-hub-web"
    exit 0
fi
exec {sys.executable} "$@"
""",
    )
    return tools


def run_setup(
    tmp_path: Path, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    app = tmp_path / "app"
    created_app = not app.exists()
    app.mkdir(exist_ok=True)
    if not (app / "setup.sh").exists():
        shutil.copy2(SETUP, app / "setup.sh")
    example = ROOT / "example-content"
    if created_app and example.is_dir():
        shutil.copytree(example, app / "example-content")
    tools = fake_setup_tools(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": str(tools),
        "PYTHONPATH": str(ROOT),
        "AGENT_HUB_SETUP_HTTP_PROBE": str(tools / "curl"),
        "AGENT_HUB_SETUP_SMOKE_PORT": "17337",
    }
    env.update(extra_env or {})
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        ["/bin/sh", str(app / "setup.sh"), *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def initialize_app_remote(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy2(SETUP, app / "setup.sh")
    (app / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    git(app, "init", "-q", "-b", "main")
    git(app, "config", "user.name", "agent-hub tests")
    git(app, "config", "user.email", "tests@example.invalid")
    git(app, "add", "setup.sh", ".gitignore")
    git(app, "commit", "-q", "-m", "initial App")
    remote = tmp_path / "app-remote.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(app), str(remote)], check=True
    )
    git(app, "remote", "add", "origin", str(remote))
    git(app, "push", "-q", "-u", "origin", "main")
    return app, remote


def publish_setup_update(tmp_path: Path, remote: Path) -> str:
    publisher = tmp_path / "publisher"
    subprocess.run(["git", "clone", "-q", str(remote), str(publisher)], check=True)
    git(publisher, "config", "user.name", "agent-hub tests")
    git(publisher, "config", "user.email", "tests@example.invalid")
    setup = publisher / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    marker = ': > "$HOME/reexecuted-updated-setup"'
    setup.write_text(
        text.replace("set -euo pipefail", f"set -euo pipefail\n\n{marker}", 1),
        encoding="utf-8",
    )
    git(publisher, "add", "setup.sh")
    git(publisher, "commit", "-q", "-m", "update App installer")
    git(publisher, "push", "-q", "origin", "main")
    return git(publisher, "rev-parse", "HEAD").stdout.strip()


def write_content_pointer(tmp_path: Path, content: Path) -> Path:
    pointer = tmp_path / "home" / ".config" / "agent-hub" / "root"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{content.resolve()}\n", encoding="utf-8")
    return pointer


def test_setup_reports_how_to_install_missing_git(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "bash").symlink_to("/bin/bash")
    result = subprocess.run(
        ["/bin/sh", str(SETUP), "--help"],
        env={"HOME": str(tmp_path / "home"), "PATH": str(tools)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "[ERROR] Git is required. Install Git from https://git-scm.com/downloads, "
        "then run setup again.\n"
    )


def test_setup_uses_local_content_without_applying_it(
    tmp_path: Path, content: Path
) -> None:
    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    pointer = tmp_path / "home" / ".config" / "agent-hub" / "root"
    assert pointer.read_text(encoding="utf-8") == f"{content.resolve()}\n"
    assert (tmp_path / "app" / ".venv" / "bin" / "agent-hub").is_file()
    assert not (tmp_path / "home" / ".config" / "agent-hub" / "peer-token").exists()
    assert "[ok] local Content:" in result.stdout
    assert "[ok] Web UI returned HTTP 200" in result.stdout
    assert f"agent-hub --repo {content.resolve()} --dry-run apply" in result.stdout
    assert "Run the Web UI in the foreground:" in result.stdout
    assert not (tmp_path / "home" / "launchctl.log").exists()


def test_macos_setup_installs_and_verifies_the_launchd_service(
    tmp_path: Path, content: Path
) -> None:
    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
        extra_env={"AGENT_HUB_TEST_UNAME": "Darwin"},
    )

    assert result.returncode == 0, result.stderr
    home = tmp_path / "home"
    plist_path = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    with plist_path.open("rb") as handle:
        service = plistlib.load(handle)
    assert service["Label"] == "com.agenthub.web"
    assert service["ProgramArguments"] == [
        str(tmp_path / "app" / ".venv" / "bin" / "agent-hub-web"),
        "--host",
        "127.0.0.1",
        "--port",
        "7337",
        "--quiet",
    ]
    assert "--repo" not in service["ProgramArguments"]
    assert service["EnvironmentVariables"] == {
        "HOME": str(home),
        "AGENT_HUB_REPO": "",
    }
    calls = (home / "launchctl.log").read_text(encoding="utf-8")
    assert "bootout gui/" in calls
    assert "bootstrap gui/" in calls
    assert str(plist_path) in calls
    assert "http://127.0.0.1:7337/" in (home / "curl.log").read_text(
        encoding="utf-8"
    )


def test_macos_setup_rerun_refreshes_only_the_app_and_service(
    tmp_path: Path, content: Path
) -> None:
    home = tmp_path / "home"
    secret_dir = home / ".config" / "agent-hub"
    secret_dir.mkdir(parents=True, exist_ok=True)
    token = secret_dir / "peer-token"
    token.write_text("keep-this-token\n", encoding="utf-8")
    args = (
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
    )
    environment = {"AGENT_HUB_TEST_UNAME": "Darwin"}

    first = run_setup(tmp_path, *args, extra_env=environment)
    assert first.returncode == 0, first.stderr
    hub = content / "config" / "hub.toml"
    content_after_first_run = hub.read_bytes()
    pointer = secret_dir / "root"
    pointer_after_first_run = pointer.read_bytes()
    plist = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    plist_after_first_run = plist.read_bytes()

    second = run_setup(tmp_path, *args, extra_env=environment)

    assert second.returncode == 0, second.stderr
    assert hub.read_bytes() == content_after_first_run
    assert pointer.read_bytes() == pointer_after_first_run
    assert token.read_text(encoding="utf-8") == "keep-this-token\n"
    assert plist.read_bytes() == plist_after_first_run
    launch_calls = (home / "launchctl.log").read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("bootout ") for call in launch_calls) == 2
    assert sum(call.startswith("bootstrap ") for call in launch_calls) == 2
    installs = (home / "venv-python.log").read_text(encoding="utf-8").splitlines()
    assert installs == [
        f"-m pip install -e {tmp_path / 'app'}",
        f"-m pip install -e {tmp_path / 'app'}",
    ]


def test_macos_uninstall_removes_only_the_service(tmp_path: Path, content: Path) -> None:
    args = (
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
    )
    environment = {"AGENT_HUB_TEST_UNAME": "Darwin"}
    installed = run_setup(tmp_path, *args, extra_env=environment)
    assert installed.returncode == 0, installed.stderr
    home = tmp_path / "home"
    token = home / ".config" / "agent-hub" / "peer-token"
    token.write_text("preserved\n", encoding="utf-8")
    content_before = (content / "config" / "hub.toml").read_bytes()
    pointer = home / ".config" / "agent-hub" / "root"
    pointer_before = pointer.read_bytes()

    result = run_setup(tmp_path, "--uninstall", extra_env=environment)

    assert result.returncode == 0, result.stderr
    assert not (
        home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    ).exists()
    assert not (home / "launchctl.loaded").exists()
    assert (tmp_path / "app" / "setup.sh").is_file()
    assert (tmp_path / "app" / ".venv" / "bin" / "agent-hub-web").is_file()
    assert (content / "config" / "hub.toml").read_bytes() == content_before
    assert pointer.read_bytes() == pointer_before
    assert token.read_text(encoding="utf-8") == "preserved\n"
    assert "uninstalled com.agenthub.web" in result.stdout


def test_linux_uninstall_reports_that_there_is_no_service(tmp_path: Path) -> None:
    result = run_setup(tmp_path, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert "no service to remove on Linux" in result.stdout
    assert not (tmp_path / "home" / "launchctl.log").exists()


def test_update_refuses_a_dirty_app_repository(tmp_path: Path, content: Path) -> None:
    app, _ = initialize_app_remote(tmp_path)
    write_content_pointer(tmp_path, content)
    (app / "setup.sh").write_text(
        (app / "setup.sh").read_text(encoding="utf-8") + "\n# local edit\n",
        encoding="utf-8",
    )
    head = git(app, "rev-parse", "HEAD").stdout.strip()

    result = run_setup(tmp_path, "--update")

    assert result.returncode == 1
    assert "App repository is dirty" in result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == head
    assert "local edit" in (app / "setup.sh").read_text(encoding="utf-8")


def test_update_refuses_an_app_repository_diverged_from_upstream(
    tmp_path: Path, content: Path
) -> None:
    app, remote = initialize_app_remote(tmp_path)
    publish_setup_update(tmp_path, remote)
    write_content_pointer(tmp_path, content)
    (app / "local.txt").write_text("local commit\n", encoding="utf-8")
    git(app, "add", "local.txt")
    git(app, "commit", "-q", "-m", "local App change")
    local_head = git(app, "rev-parse", "HEAD").stdout.strip()

    result = run_setup(tmp_path, "--update")

    assert result.returncode == 1
    assert "App repository has diverged from its upstream" in result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == local_head
    assert not (tmp_path / "home" / "reexecuted-updated-setup").exists()


def test_update_refuses_an_app_repository_with_local_commits(
    tmp_path: Path, content: Path
) -> None:
    app, _ = initialize_app_remote(tmp_path)
    write_content_pointer(tmp_path, content)
    (app / "local.txt").write_text("local commit\n", encoding="utf-8")
    git(app, "add", "local.txt")
    git(app, "commit", "-q", "-m", "local App change")
    local_head = git(app, "rev-parse", "HEAD").stdout.strip()

    result = run_setup(tmp_path, "--update")

    assert result.returncode == 1
    assert "App repository has local commits that update will not touch" in result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == local_head
    assert not (tmp_path / "home" / "reexecuted-updated-setup").exists()


def test_update_validates_the_content_pointer_before_pulling(tmp_path: Path) -> None:
    app, remote = initialize_app_remote(tmp_path)
    publish_setup_update(tmp_path, remote)
    local_head = git(app, "rev-parse", "HEAD").stdout.strip()

    result = run_setup(tmp_path, "--update")

    assert result.returncode == 1
    assert "cannot read Content pointer" in result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == local_head
    assert not (tmp_path / "home" / "git.log").exists()


def test_update_reports_rev_list_stderr_before_parsing_output(
    tmp_path: Path, content: Path
) -> None:
    app, _ = initialize_app_remote(tmp_path)
    write_content_pointer(tmp_path, content)
    local_head = git(app, "rev-parse", "HEAD").stdout.strip()
    error = "forced rev-list failure"

    result = run_setup(
        tmp_path,
        "--update",
        extra_env={"AGENT_HUB_TEST_GIT_REV_LIST_ERROR": error},
    )

    assert result.returncode == 1
    assert error in result.stderr
    assert "could not compare the App with its upstream" not in result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == local_head


def test_update_fast_forwards_reexecutes_and_preserves_content(
    tmp_path: Path, content: Path
) -> None:
    app, remote = initialize_app_remote(tmp_path)
    remote_head = publish_setup_update(tmp_path, remote)
    home = tmp_path / "home"
    secret_dir = home / ".config" / "agent-hub"
    secret_dir.mkdir(parents=True, exist_ok=True)
    pointer = write_content_pointer(tmp_path, content)
    token = secret_dir / "peer-token"
    token.write_text("do-not-change\n", encoding="utf-8")
    content_head = git(content, "rev-parse", "HEAD").stdout
    content_status = git(content, "status", "--porcelain").stdout
    content_config = (content / "config" / "hub.toml").read_bytes()

    result = run_setup(
        tmp_path,
        "--update",
        extra_env={"AGENT_HUB_TEST_UNAME": "Darwin"},
    )

    assert result.returncode == 0, result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == remote_head
    assert (home / "reexecuted-updated-setup").is_file()
    assert f"-C {app} pull --ff-only" in (home / "git.log").read_text(
        encoding="utf-8"
    )
    assert pointer.read_text(encoding="utf-8") == f"{content.resolve()}\n"
    assert token.read_text(encoding="utf-8") == "do-not-change\n"
    assert git(content, "rev-parse", "HEAD").stdout == content_head
    assert git(content, "status", "--porcelain").stdout == content_status
    assert (content / "config" / "hub.toml").read_bytes() == content_config
    plist = home / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    assert plist.is_file()
    assert "bootstrap gui/" in (home / "launchctl.log").read_text(encoding="utf-8")
    assert "http://127.0.0.1:7337/" in (home / "curl.log").read_text(
        encoding="utf-8"
    )


def test_linux_update_refreshes_the_app_without_a_service(
    tmp_path: Path, content: Path
) -> None:
    app, remote = initialize_app_remote(tmp_path)
    remote_head = publish_setup_update(tmp_path, remote)
    write_content_pointer(tmp_path, content)

    result = run_setup(tmp_path, "--update")

    assert result.returncode == 0, result.stderr
    assert git(app, "rev-parse", "HEAD").stdout.strip() == remote_head
    assert (app / ".venv" / "bin" / "agent-hub-web").is_file()
    assert not (tmp_path / "home" / "launchctl.log").exists()
    assert not (
        tmp_path / "home" / "Library" / "LaunchAgents" / "com.agenthub.web.plist"
    ).exists()
    assert (
        f"Run the Web UI in the foreground: {app / '.venv' / 'bin' / 'agent-hub-web'} "
        "--host 127.0.0.1 --port 7337"
    ) in result.stdout


def test_new_content_stops_with_repo_local_git_identity_instructions(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "new-content"
    result = run_setup(
        tmp_path,
        "--new-content",
        str(destination),
        "--machine",
        "newmachine",
        "--non-interactive",
    )

    assert result.returncode == 1
    app = tmp_path / "app"
    assert f"git -C {app} config user.name" in result.stderr
    assert f"git -C {app} config user.email" in result.stderr
    assert not destination.exists()


def test_setup_creates_and_commits_new_content(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Setup Test\n\temail = setup@example.invalid\n",
        encoding="utf-8",
    )
    destination = tmp_path / "new-content"

    result = run_setup(
        tmp_path,
        "--new-content",
        str(destination),
        "--machine",
        "newmachine",
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    assert subprocess.run(
        ["git", "-C", str(destination), "branch", "--show-current"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip() == "main"
    assert subprocess.run(
        ["git", "-C", str(destination), "log", "--format=%s"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.splitlines() == ["Create agent-hub Content"]
    assert subprocess.run(
        ["git", "-C", str(destination), "status", "--porcelain"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout == ""
    hub = (destination / "config" / "hub.toml").read_text(encoding="utf-8")
    assert ' = "newmachine"' in hub
    assert (destination / "skills" / "global" / "example" / "SKILL.md").is_file()


def test_setup_clones_content_to_a_new_destination(
    tmp_path: Path, content: Path
) -> None:
    destination = tmp_path / "cloned-content"
    result = run_setup(
        tmp_path,
        "--content-url",
        str(content),
        "--content-dir",
        str(destination),
        "--machine",
        "testmachine",
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    assert (destination / ".git").is_dir()
    assert (tmp_path / "home" / ".config" / "agent-hub" / "root").read_text(
        encoding="utf-8"
    ) == f"{destination}\n"


def test_setup_does_not_overwrite_a_clone_destination(
    tmp_path: Path, content: Path
) -> None:
    destination = tmp_path / "cloned-content"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    result = run_setup(
        tmp_path,
        "--content-url",
        str(content),
        "--content-dir",
        str(destination),
        "--machine",
        "testmachine",
        "--non-interactive",
    )

    assert result.returncode == 1
    assert "refusing to overwrite existing clone destination" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_setup_accepts_a_peer_token_file_outside_git(
    tmp_path: Path, content: Path
) -> None:
    source = tmp_path / "shared-token"
    source.write_text("shared-secret\n", encoding="utf-8")

    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--peer-token-file",
        str(source),
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    token = tmp_path / "home" / ".config" / "agent-hub" / "peer-token"
    assert token.read_text(encoding="utf-8") == "shared-secret\n"
    assert token.stat().st_mode & 0o777 == 0o600
    assert not (content / "peer-token").exists()


def test_setup_requires_an_explicit_content_choice_when_unattended(tmp_path: Path) -> None:
    result = run_setup(tmp_path, "--machine", "testmachine", "--non-interactive")

    assert result.returncode == 1
    assert (
        "choose Content with --content PATH, --content-url URL, or --new-content PATH"
        in result.stderr
    )


def test_setup_registers_a_machine_without_committing_existing_content(
    tmp_path: Path, content: Path
) -> None:
    hub = content / "config" / "hub.toml"
    hub.write_text('[machines]\n"retired-host" = "existing"', encoding="utf-8")
    git(content, "add", "config/hub.toml")
    git(content, "commit", "-q", "-m", "remove local machine")
    head = git(content, "rev-parse", "HEAD").stdout.strip()

    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "existing",
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    hub_data = tomllib.loads(hub.read_text(encoding="utf-8"))
    assert set(hub_data["machines"].values()) == {"existing"}
    assert git(content, "rev-parse", "HEAD").stdout.strip() == head
    assert git(content, "status", "--porcelain").stdout == " M config/hub.toml\n"


def test_setup_can_generate_a_peer_token_outside_git(
    tmp_path: Path, content: Path
) -> None:
    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--generate-peer-token",
        "--non-interactive",
    )

    assert result.returncode == 0, result.stderr
    token = tmp_path / "home" / ".config" / "agent-hub" / "peer-token"
    value = token.read_text(encoding="utf-8").strip()
    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)
    assert token.stat().st_mode & 0o777 == 0o600


def test_setup_reports_how_to_install_missing_bash(tmp_path: Path) -> None:
    shell = next(
        candidate
        for candidate in (Path("/bin/dash"), Path("/usr/bin/dash"), Path("/bin/zsh"))
        if candidate.is_file()
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    result = subprocess.run(
        [str(shell), str(SETUP), "--help"],
        env={"HOME": str(tmp_path / "home"), "PATH": str(tools)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "[ERROR] Bash is required. Install Bash from "
        "https://www.gnu.org/software/bash/, then run setup again.\n"
    )


def test_setup_reports_how_to_install_a_supported_python(tmp_path: Path) -> None:
    tools = fake_setup_tools(tmp_path)
    unsupported = tools / "unsupported-python"
    write_executable(unsupported, "#!/bin/sh\nexit 1\n")
    result = subprocess.run(
        ["/bin/sh", str(SETUP), "--help"],
        env={
            "AGENT_HUB_SETUP_PYTHON": str(unsupported),
            "HOME": str(tmp_path / "home"),
            "PATH": str(tools),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "[ERROR] Python 3.11 or newer is required. Install it from "
        "https://www.python.org/downloads/, then run setup again.\n"
    )


def test_setup_reports_status_failure(tmp_path: Path, content: Path) -> None:
    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
        extra_env={"AGENT_HUB_TEST_STATUS_EXIT": "2"},
    )

    assert result.returncode == 1
    assert "agent-hub status failed" in result.stderr
    assert "Web UI returned HTTP 200" not in result.stdout


def test_setup_reports_web_process_failure(tmp_path: Path, content: Path) -> None:
    result = run_setup(
        tmp_path,
        "--content",
        str(content),
        "--machine",
        "testmachine",
        "--non-interactive",
        extra_env={"AGENT_HUB_TEST_WEB_FAIL": "1"},
    )

    assert result.returncode == 1
    assert "forced Web failure" in result.stderr
    assert "Web UI returned HTTP 200" not in result.stdout
