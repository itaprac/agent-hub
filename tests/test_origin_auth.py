"""Origin authentication uses fake keys/API replies and a temporary Git Store."""

import base64
import hashlib
import json
from pathlib import Path
import shlex
import stat
import struct
import subprocess

import pytest

from agenthub import origin_auth
from conftest import git, write

SLUG = "example/store"
ORIGIN = "https://github.com/example/store.git"
BLOB = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
PUBLIC = "ssh-ed25519 " + base64.b64encode(BLOB).decode()
HOST_BLOB = (
    struct.pack(">I", 11)
    + b"ssh-ed25519"
    + struct.pack(">I", 32)
    + bytes(reversed(range(32)))
)
HOST_KEY = "ssh-ed25519 " + base64.b64encode(HOST_BLOB).decode()


def keys(home):
    key = (
        home
        / ".ssh"
        / ("agent-hub-origin-" + hashlib.sha256(SLUG.encode()).hexdigest()[:20])
    )
    return key, Path(str(key) + ".pub")


def existing_key(home, comment=None):
    key, public = keys(home)
    write(key, "PRIVATE-KEY-MUST-NOT-BE-LOGGED")
    write(public, PUBLIC + " " + (comment or "agent-hub-origin:" + SLUG) + "\n")
    key.chmod(0o600)
    public.chmod(0o600)


@pytest.fixture
def tools(content, home, monkeypatch):
    git(content, "remote", "add", "origin", ORIGIN)
    real_run = subprocess.run
    state = {"calls": [], "keys": [], "generated": 0, "granted": 0, "fail": None}

    def run(args, **kwargs):
        state["calls"].append(args)
        assert not kwargs.get("shell", False)
        if args[0] == "git":
            if "ls-remote" in args:
                assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
                assert args[-2:] == ["git@github.com:example/store.git", "HEAD"]
                assert "StrictHostKeyChecking=yes" in args[2]
                return subprocess.CompletedProcess(
                    args,
                    1 if state["fail"] == "ssh" else 0,
                    "fixture\tHEAD\n",
                    "SECRET FAILURE",
                )
            return real_run(args, **kwargs)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"]["GH_HOST"] == "github.com"
        if args[0] == "ssh-keygen":
            if "-y" in args:
                return subprocess.CompletedProcess(args, 0, PUBLIC + "\n", "")
            state["generated"] += 1
            key = Path(args[args.index("-f") + 1])
            write(key, "PRIVATE-KEY-MUST-NOT-BE-LOGGED")
            write(
                Path(str(key) + ".pub"),
                PUBLIC + " " + args[args.index("-C") + 1] + "\n",
            )
            return subprocess.CompletedProcess(args, 0, "", "")
        assert args[0] == "gh"
        if "meta" in args:
            if state["fail"] == "meta":
                return subprocess.CompletedProcess(
                    args, 1, "TOKEN SECRET", "TOKEN SECRET"
                )
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"ssh_keys": [HOST_KEY, "ssh-rsa ignored"]}), ""
            )
        if "deploy-key" in args:
            assert "--allow-write" in args and args[-2:] == ["--repo", SLUG]
            state["granted"] += 1
            if state["fail"] == "grant":
                return subprocess.CompletedProcess(
                    args, 1, "TOKEN SECRET", "TOKEN SECRET"
                )
            state["keys"] = [{"key": PUBLIC, "read_only": False}]
            return subprocess.CompletedProcess(args, 0, "", "")
        assert args[-2:] == ["--paginate", "--slurp"]
        return subprocess.CompletedProcess(args, 0, json.dumps([state["keys"]]), "")

    monkeypatch.setattr(origin_auth.subprocess, "run", run)
    return state


def test_configure_uses_write_deploy_key_and_backs_up_git_config(content, home, tools):
    config_before = (content / ".git/config").read_bytes()
    original_hosts = b"# local entry\xff\nlocal.example ssh-ed25519 EXISTING"
    (home / ".ssh").mkdir()
    (home / ".ssh/known_hosts").write_bytes(original_hosts)

    result = origin_auth.configure(content)

    assert result.exit_code == 0, result.lines()
    assert result.command == "remote origin-auth"
    assert tools["generated"] == tools["granted"] == 1
    key, public = keys(home)
    for path in (key, public):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (backup,) = (content / ".git").glob("config.agent-hub-origin-backup-*")
    assert backup.read_bytes() == config_before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert (
        home / ".ssh/known_hosts"
    ).read_bytes() == original_hosts + b"\n" + f"github.com {HOST_KEY}\n".encode()
    assert (
        git(content, "config", "--local", "remote.origin.url").stdout.strip()
        == "git@github.com:example/store.git"
    )
    configured = shlex.split(
        git(content, "config", "--local", "core.sshCommand").stdout.strip()
    )
    assert configured[configured.index("-i") + 1] == str(key)
    assert "IdentitiesOnly=yes" in configured and "IdentityAgent=none" in configured
    assert "PRIVATE-KEY" not in json.dumps(result.to_dict())
    assert "TOKEN" not in json.dumps(result.to_dict())


def test_second_configuration_is_idempotent(content, home, tools):
    first = origin_auth.configure(content)
    assert first.exit_code == 0, first.lines()
    before = (content / ".git/config").read_bytes()
    hosts_before = (home / ".ssh/known_hosts").read_bytes()
    backups_before = list((content / ".git").glob("config.agent-hub-origin-backup-*"))
    second = origin_auth.configure(content)
    assert second.exit_code == 0, second.lines()
    assert tools["generated"] == tools["granted"] == 1
    assert (content / ".git/config").read_bytes() == before
    assert (home / ".ssh/known_hosts").read_bytes() == hosts_before
    assert (
        list((content / ".git").glob("config.agent-hub-origin-backup-*"))
        == backups_before
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/example/store;touch",
        "https://token@github.com/example/store.git",
        "git@github.com:example/store.git\n-oProxyCommand=attack",
        "https://other.example/example/store.git",
        "file:///tmp/store",
        "https://github.com/example/../store",
        "ssh://-oProxyCommand@github.com/example/store",
    ],
)
def test_invalid_origin_stops_before_key_or_api_changes(content, home, tools, origin):
    git(content, "config", "--local", "remote.origin.url", origin)
    before = (content / ".git/config").read_bytes()
    result = origin_auth.configure(content)
    assert result.exit_code == 1
    assert tools["generated"] == tools["granted"] == 0
    assert not any(args[0] == "gh" for args in tools["calls"])
    assert not keys(home)[0].exists()
    assert (content / ".git/config").read_bytes() == before


def test_existing_read_only_deploy_key_is_not_upgraded(content, home, tools):
    existing_key(home)
    tools["keys"] = [{"key": PUBLIC, "read_only": True}]
    before = (content / ".git/config").read_bytes()
    result = origin_auth.configure(content)
    assert result.exit_code == 1
    assert "read-only" in str(result.lines())
    assert tools["generated"] == tools["granted"] == 0
    assert (content / ".git/config").read_bytes() == before
    assert not (home / ".ssh/known_hosts").exists()


def test_unknown_existing_key_and_custom_ssh_command_are_preserved(
    content, home, tools
):
    existing_key(home, comment="unrelated-user-key")
    result = origin_auth.configure(content)
    assert result.exit_code == 1 and "not owned" in str(result.lines())
    assert tools["generated"] == tools["granted"] == 0
    assert keys(home)[0].read_text() == "PRIVATE-KEY-MUST-NOT-BE-LOGGED"
    git(content, "config", "--local", "core.sshCommand", "ssh -i /custom/key")
    result = origin_auth.configure(content)
    assert result.exit_code == 1 and "different local" in str(result.lines())
    assert (
        git(content, "config", "--local", "core.sshCommand").stdout.strip()
        == "ssh -i /custom/key"
    )


@pytest.mark.parametrize("failure", ["meta", "grant", "ssh"])
def test_failures_never_change_origin_and_do_not_log_secrets(
    content, home, tools, failure
):
    tools["fail"] = failure
    before = (content / ".git/config").read_bytes()
    result = origin_auth.configure(content)
    assert result.exit_code == 1
    assert (content / ".git/config").read_bytes() == before
    serialized = json.dumps(result.to_dict())
    assert "origin URL was not changed" in serialized
    assert "SECRET" not in serialized and "PRIVATE-KEY" not in serialized
    if failure == "ssh":
        assert tools["granted"] == 1
        assert keys(home)[0].is_file()


def test_symlinked_known_hosts_is_not_changed(content, home, tools):
    outside = home / "outside-hosts"
    write(outside, "untouched")
    (home / ".ssh").mkdir()
    (home / ".ssh/known_hosts").symlink_to(outside)
    result = origin_auth.configure(content)
    assert result.exit_code == 1
    assert outside.read_text() == "untouched"
    assert tools["generated"] == tools["granted"] == 0


def test_explicit_push_url_stops_before_key_or_configuration_changes(
    content, home, tools
):
    git(content, "config", "--local", "remote.origin.pushurl", ORIGIN)
    before = (content / ".git/config").read_bytes()
    result = origin_auth.configure(content)
    assert result.exit_code == 1
    assert "explicit origin push URL" in str(result.lines())
    assert (content / ".git/config").read_bytes() == before
    assert not any(args[0] == "gh" for args in tools["calls"])
    assert tools["generated"] == tools["granted"] == 0
