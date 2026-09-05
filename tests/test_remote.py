"""SSH actions use isolated local configuration and never contact a host."""

import json
from pathlib import Path
import shlex
import subprocess

import pytest

from agenthub import remote
from conftest import write

TARGET = {
    "destination": "user@host.example",
    "executable": "/Users/user/.local/bin/agent-hub",
    "store": "/Users/user/.agents",
}


def configure(home: Path, target=None, machine="remote-machine"):
    write(
        home / ".config/agent-hub/remotes.json",
        json.dumps({machine: TARGET if target is None else target}),
    )


def report(command="status", *, machine="remote-machine", code=0):
    return {
        "command": command,
        "machine_id": machine,
        "hostname": "remote-host",
        "repo": "/Users/user/Store",
        "exit_code": code,
        "problems": int(code != 0),
        "checks": [
            {"kind": "git", "level": "DRIFT" if code else "ok", "text": "fixture"}
        ],
        "lines": [{"level": "DRIFT" if code else "ok", "text": "fixture"}],
    }


@pytest.fixture
def ssh(monkeypatch):
    calls = []
    replies = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, subprocess.CompletedProcess):
            return reply
        return subprocess.CompletedProcess(
            args, reply["exit_code"], json.dumps(reply), ""
        )

    monkeypatch.setattr(remote.subprocess, "run", run)
    return calls, replies


def test_configuration_is_local_and_listing_does_not_contact_ssh(home, content, ssh):
    write(content / "remotes.json", json.dumps({"store-supplied": TARGET}))
    assert remote.configured_machines() == set()
    configure(home)
    assert remote.configured_machines() == {"remote-machine"}
    assert remote.load_remotes()["remote-machine"].destination == TARGET["destination"]
    assert ssh[0] == []


@pytest.mark.parametrize(
    "destination",
    [
        "-oProxyCommand=touch",
        "user@host;touch",
        "user@host\nother",
        "user@@host",
        "host $(touch x)",
        "user@host/other",
        "-user@host",
        "host..example",
    ],
)
def test_rejects_ssh_destination_injection(home, destination, ssh):
    configure(home, dict(TARGET, destination=destination))
    with pytest.raises(remote.RemoteError):
        remote.run("remote-machine", "sync")
    assert ssh[0] == []


@pytest.mark.parametrize(
    "change",
    [
        {"executable": "agent-hub"},
        {"store": "~/Store"},
        {"identity_file": "relative"},
        {"identity_file": None},
        {"executable": "/bin/tool\nrm"},
        {"port": 22},
        {"store": 7},
    ],
)
def test_rejects_invalid_remote_fields(home, change, ssh):
    configure(home, dict(TARGET, **change))
    with pytest.raises(remote.RemoteError):
        remote.load_remotes()
    assert ssh[0] == []


def test_quotes_remote_paths_and_uses_noninteractive_ssh_options(home, ssh):
    calls, replies = ssh
    target = dict(
        TARGET,
        executable="/Users/user/a 'quoted';$(touch marker)/agent-hub",
        store="/Users/user/Store with spaces;$(touch marker)",
        identity_file="/Users/local/keys/key with spaces",
    )
    configure(home, target)
    replies.extend([report(), report("--dry-run sync")])
    result = remote.run("remote-machine", "sync", dry_run=True)
    assert result["command"] == "--dry-run sync"
    for args, options in calls:
        assert args[0:2] == ["ssh", "-T"]
        for setting in (
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "ConnectTimeout=5",
            "ServerAliveInterval=15",
            "ServerAliveCountMax=2",
            "IdentitiesOnly=yes",
        ):
            assert setting in args
        assert args[args.index("-i") + 1] == target["identity_file"]
        assert options["stdin"] == subprocess.DEVNULL
        assert options["timeout"] == remote.SSH_TIMEOUT
        assert not options.get("shell", False)
    assert shlex.split(calls[0][0][-1]) == [
        target["executable"],
        "--store",
        target["store"],
        "status",
        "--json",
    ]
    assert shlex.split(calls[1][0][-1]) == [
        target["executable"],
        "--store",
        target["store"],
        "sync",
        "--json",
        "--dry-run",
    ]
    assert not (home / "marker").exists()


def test_default_ssh_does_not_restrict_identity_selection(home, ssh):
    configure(home)
    ssh[1].extend([report(), report("apply")])
    remote.run("remote-machine", "apply")
    assert "-i" not in ssh[0][0][0]
    assert "IdentitiesOnly=yes" not in ssh[0][0][0]


def test_identity_mismatch_prevents_mutation(home, ssh):
    configure(home)
    ssh[1].append(report(machine="other-machine"))
    with pytest.raises(remote.RemoteError, match="Machine ID"):
        remote.run("remote-machine", "sync")
    assert len(ssh[0]) == 1


def test_remote_status_configuration_failure_prevents_mutation(home, ssh):
    configure(home)
    ssh[1].append(report(code=2))
    with pytest.raises(remote.RemoteError, match="configuration error"):
        remote.run("remote-machine", "apply")
    assert len(ssh[0]) == 1


def test_drift_status_allows_repair_and_nonzero_command_report_is_returned(home, ssh):
    configure(home)
    result = report("sync", code=1)
    ssh[1].extend([report(code=1), result])
    assert remote.run("remote-machine", "sync") == result
    assert len(ssh[0]) == 2


@pytest.mark.parametrize("machine", ["unknown", "testmachine"])
def test_unknown_and_local_targets_do_not_contact_ssh(home, ssh, machine):
    configure(home, machine="testmachine")
    with pytest.raises(remote.RemoteError):
        remote.run(machine, "sync")
    assert ssh[0] == []


@pytest.mark.parametrize(
    "command,dry_run",
    [("install", False), ("sync;touch", False), ("sync", "false"), ([], False)],
)
def test_invalid_operation_is_rejected_before_ssh(home, ssh, command, dry_run):
    configure(home)
    with pytest.raises(remote.RemoteError):
        remote.run("remote-machine", command, dry_run)
    assert ssh[0] == []


@pytest.mark.parametrize(
    "output", ["not json", "[]", '{"machine_id":"remote-machine"}']
)
def test_invalid_status_json_prevents_mutation(home, ssh, output):
    configure(home)
    ssh[1].append(subprocess.CompletedProcess([], 0, output, "secret transport detail"))
    with pytest.raises(remote.RemoteError) as caught:
        remote.run("remote-machine", "sync")
    assert "secret" not in str(caught.value)
    assert len(ssh[0]) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("lines", ["bad"]),
        ("checks", [{"kind": "git", "level": "ok", "text": 7}]),
        ("exit_code", True),
        ("problems", -1),
        ("command", "sync"),
    ],
)
def test_invalid_report_fields_prevent_mutation(home, ssh, field, value):
    configure(home)
    output = report()
    output[field] = value
    ssh[1].append(subprocess.CompletedProcess([], 0, json.dumps(output), ""))
    with pytest.raises(remote.RemoteError):
        remote.run("remote-machine", "apply")
    assert len(ssh[0]) == 1


def test_transport_failure_is_safe_and_not_retried(home, ssh):
    configure(home)
    ssh[1].append(subprocess.CompletedProcess([], 255, "", "sensitive auth detail"))
    with pytest.raises(remote.RemoteError, match="SSH connection failed") as caught:
        remote.run("remote-machine", "sync")
    assert "sensitive" not in str(caught.value)
    assert len(ssh[0]) == 1


def test_mutation_timeout_does_not_retry_or_claim_remote_cancellation(home, ssh):
    configure(home)
    ssh[1].extend([report(), subprocess.TimeoutExpired("private command", 180)])
    with pytest.raises(remote.RemoteError, match="may still be running") as caught:
        remote.run("remote-machine", "sync")
    assert "private command" not in str(caught.value)
    assert len(ssh[0]) == 2


def test_duplicate_config_keys_and_symlinked_config_are_rejected(home, ssh):
    path = home / ".config/agent-hub/remotes.json"
    write(path, '{"target":{},"target":{}}')
    with pytest.raises(remote.RemoteError, match="duplicate"):
        remote.load_remotes()
    path.unlink()
    target = home / "store-config.json"
    write(target, json.dumps({"remote-machine": TARGET}))
    path.symlink_to(target)
    with pytest.raises(remote.RemoteError, match="symlinks"):
        remote.load_remotes()
    assert ssh[0] == []


@pytest.mark.parametrize("code", [0, 1])
def test_check_verifies_identity_without_requesting_a_write(home, ssh, code):
    configure(home)
    ssh[1].append(report(code=code))
    checked = remote.check("remote-machine")
    assert checked.machine == "remote-machine"
    assert checked.target == remote.RemoteTarget(**TARGET)
    assert len(ssh[0]) == 1
    assert shlex.split(ssh[0][0][0][-1])[3:] == ["status", "--json"]


@pytest.mark.parametrize("status", [report(machine="wrong-machine"), report(code=2)])
def test_check_rejects_wrong_identity_and_configuration_errors(home, ssh, status):
    configure(home)
    ssh[1].append(status)
    with pytest.raises(remote.RemoteError):
        remote.check("remote-machine")
    assert len(ssh[0]) == 1


@pytest.mark.parametrize("command,dry_run", [("apply", False), ("sync", True)])
def test_checked_target_runs_without_a_second_preflight(home, ssh, command, dry_run):
    configure(home)
    expected = report(f"--dry-run {command}" if dry_run else command)
    ssh[1].extend([report(), expected])
    checked = remote.check("remote-machine")
    # The local operation must use exactly the target whose identity was checked.
    configure(home, {**TARGET, "destination": "changed.example"})
    result = remote.run("remote-machine", command, dry_run, checked_target=checked)
    assert result == expected
    assert len(ssh[0]) == 2
    assert [shlex.split(call[0][-1])[3] for call in ssh[0]] == ["status", command]
    assert all(call[0][-2] == TARGET["destination"] for call in ssh[0])


def test_checked_target_cannot_be_used_for_another_machine(home, ssh):
    configure(home)
    ssh[1].append(report())
    checked = remote.check("remote-machine")
    with pytest.raises(remote.RemoteError, match="does not match"):
        remote.run("other-machine", "sync", checked_target=checked)
    assert len(ssh[0]) == 1


@pytest.mark.parametrize(
    "machine,command,dry_run",
    [
        ("INVALID", "sync", False),
        ("remote-machine", "install", False),
        ("remote-machine", "sync", "false"),
    ],
)
def test_checked_target_still_validates_operation_inputs(
    home, ssh, machine, command, dry_run
):
    configure(home)
    ssh[1].append(report())
    checked = remote.check("remote-machine")
    with pytest.raises(remote.RemoteError):
        remote.run(machine, command, dry_run, checked_target=checked)
    assert len(ssh[0]) == 1


def test_checked_target_still_validates_remote_result_identity(home, ssh):
    configure(home)
    ssh[1].extend([report(), report("apply", machine="other-machine")])
    checked = remote.check("remote-machine")
    with pytest.raises(remote.RemoteError, match="Machine ID"):
        remote.run("remote-machine", "apply", checked_target=checked)
    assert len(ssh[0]) == 2
