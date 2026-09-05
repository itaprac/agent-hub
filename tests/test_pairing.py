"""Pairing grants only one controller access to fixed Store commands."""

import base64
import json
import os
from pathlib import Path
import shlex
import stat
import struct
import subprocess
import sys

import pytest

from agenthub import pairing

BLOB = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
ENCODED = base64.b64encode(BLOB).decode()
KEY = "ssh-ed25519 " + ENCODED + " fixture key"
CONTROLLER = "100.64.1.2"


@pytest.fixture
def executable(home: Path) -> Path:
    path = home / "tool bin/agent-hub"
    path.parent.mkdir()
    path.write_text(
        f"#!{sys.executable}\nimport json,os,sys\nprint(json.dumps({{'args':sys.argv,'home':os.environ['HOME'],'git_dir':os.environ.get('GIT_DIR'),'pythonpath':os.environ.get('PYTHONPATH')}}))\n"
    )
    path.chmod(0o755)
    return path.resolve()


def authorize(home, content, executable, key=KEY, controller=CONTROLLER):
    report = pairing.trust(key, controller, content, executable)
    assert report.exit_code == 0, report.lines()
    (wrapper,) = (home / ".local/share/agent-hub").glob("remote-command-*.py")
    return wrapper


def run_wrapper(wrapper, args, **environment):
    return subprocess.run(
        [sys.executable, "-I", str(wrapper)],
        env={
            **os.environ,
            "SSH_ORIGINAL_COMMAND": shlex.join([str(arg) for arg in args]),
            **environment,
        },
        text=True,
        capture_output=True,
    )


def test_append_preserves_bytes_backs_up_and_is_idempotent(home, content, executable):
    authorized = home / ".ssh/authorized_keys"
    authorized.parent.mkdir()
    original = b"# Existing keys\xff\nssh-rsa OTHER unrelated"
    authorized.write_bytes(original)
    wrapper = authorize(home, content, executable)
    first = authorized.read_bytes()
    assert first.startswith(original + b"\n")
    assert b'restrict,from="100.64.1.2",command="' in first
    assert b" -I " in first
    assert len(first.splitlines()) == 3
    (backup,) = authorized.parent.glob("authorized_keys.agent-hub-backup-*")
    assert backup.read_bytes() == original
    for path in (authorized, wrapper, backup):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (authorized.parent, wrapper.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    authorize(home, content, executable)
    assert authorized.read_bytes() == first
    assert list(authorized.parent.glob("authorized_keys.agent-hub-backup-*")) == [
        backup
    ]


@pytest.mark.parametrize(
    "command,dry",
    [
        ("status", False),
        ("apply", False),
        ("sync", False),
        ("apply", True),
        ("sync", True),
    ],
)
def test_wrapper_runs_only_fixed_cli_and_store(home, content, executable, command, dry):
    wrapper = authorize(home, content, executable)
    exe_alias = home / "cli-alias"
    exe_alias.symlink_to(executable)
    repo_alias = home / "store-alias"
    repo_alias.symlink_to(content, target_is_directory=True)
    args = [str(exe_alias), "--store", str(repo_alias), command, "--json"] + (
        ["--dry-run"] if dry else []
    )
    result = run_wrapper(
        wrapper, args, GIT_DIR="/untrusted/repo", PYTHONPATH="/untrusted/modules"
    )
    assert result.returncode == 0, result.stderr
    received = json.loads(result.stdout)
    assert (
        received["args"]
        == [str(executable), "--store", str(content.resolve()), command, "--json"]
        + args[5:]
    )
    assert received["home"] == str(home.resolve())
    assert received["git_dir"] is None and received["pythonpath"] is None


@pytest.mark.parametrize(
    "attack",
    [
        "shell",
        "semicolon",
        "substitution",
        "store",
        "executable",
        "extra",
        "missing-json",
        "dry-status",
        "prefer",
        "install",
        "empty",
        "unbalanced",
        "relative",
    ],
)
def test_wrapper_denies_command_and_argument_injection(
    home, content, executable, attack
):
    wrapper = authorize(home, content, executable)
    args = [str(executable), "--store", str(content), "sync", "--json"]
    if attack == "shell":
        args = ["/bin/sh", "-c", "echo unsafe"]
    elif attack == "semicolon":
        args += [";", "touch", str(home / "escaped")]
    elif attack == "substitution":
        args[0] = "$(touch " + str(home / "escaped") + ")"
    elif attack == "store":
        args[2] = str(home)
    elif attack == "executable":
        args[0] = "/bin/echo"
    elif attack == "extra":
        args += ["--store", str(home)]
    elif attack == "missing-json":
        args.pop()
    elif attack == "dry-status":
        args[3] = "status"
        args.append("--dry-run")
    elif attack == "prefer":
        args += ["--prefer", "remote"]
    elif attack == "install":
        args[3] = "install"
    elif attack == "empty":
        args = []
    elif attack == "relative":
        args[2] = "."
    result = run_wrapper(
        wrapper,
        args,
        **({"SSH_ORIGINAL_COMMAND": "'unbalanced"} if attack == "unbalanced" else {}),
    )
    assert result.returncode == 126, result.stdout + result.stderr
    assert "SSH command denied" in result.stderr
    assert not (home / "escaped").exists()


@pytest.mark.parametrize(
    "key",
    [
        "ssh-rsa " + ENCODED,
        'command="sh" ' + KEY,
        KEY + "\n" + KEY,
        "ssh-ed25519 !!!",
        base64.b64encode(b"ssh-ed25519 wrong").decode(),
        "",
    ],
)
def test_malformed_key_does_not_create_authorization(home, content, executable, key):
    report = pairing.trust(key, CONTROLLER, content, executable)
    assert report.exit_code == 1
    assert not (home / ".ssh").exists()


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "100.63.255.255",
        "100.128.0.0",
        "example.com",
        "100.64.1.2,127.0.0.1",
        '100.64.1.2"',
        "::1",
    ],
)
def test_only_tailscale_controller_addresses_are_allowed(home, content, executable, ip):
    assert pairing.trust(KEY, ip, content, executable).exit_code == 1
    assert not (home / ".ssh").exists()


def test_base64_key_and_tailscale_ipv6(home, content, executable):
    authorize(home, content, executable, key=ENCODED, controller="fd7a:115c:a1e0::1234")
    assert (
        b'from="fd7a:115c:a1e0::1234"' in (home / ".ssh/authorized_keys").read_bytes()
    )


@pytest.mark.parametrize(
    "unsafe", [".ssh", ".ssh/authorized_keys", ".local", ".local/share/agent-hub"]
)
def test_symlink_parents_and_files_are_untouched(
    home, content, executable, tmp_path, unsafe
):
    outside = tmp_path / "outside"
    if unsafe.endswith("authorized_keys"):
        outside.write_bytes(b"existing external key\n")
    else:
        outside.mkdir()
    path = home / unsafe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    report = pairing.trust(KEY, CONTROLLER, content, executable)
    assert report.exit_code == 1
    assert path.is_symlink()
    assert (
        outside.read_bytes() == b"existing external key\n"
        if outside.is_file()
        else list(outside.iterdir()) == []
    )


def test_existing_unrestricted_duplicate_is_not_reported_as_trusted(
    home, content, executable
):
    authorize(home, content, executable)
    authorized = home / ".ssh/authorized_keys"
    authorized.write_bytes(authorized.read_bytes() + KEY.encode() + b"\n")
    before = authorized.read_bytes()
    report = pairing.trust(KEY, CONTROLLER, content, executable)
    assert report.exit_code == 1
    assert authorized.read_bytes() == before


def test_same_key_cannot_silently_change_store(home, content, executable):
    wrapper = authorize(home, content, executable)
    before = wrapper.read_bytes()
    other = home / "other-store"
    other.mkdir()
    assert pairing.trust(KEY, CONTROLLER, other, executable).exit_code == 1
    assert wrapper.read_bytes() == before
