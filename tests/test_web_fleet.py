"""The Fleet API reads committed records through the serialized Store interface."""

import json
import urllib.error
import urllib.request

import pytest

from agenthub import operations


def test_fleet_returns_local_identity_and_records(server, monkeypatch):
    records = [{"machine": "remote", "current": False, "behind": 2, "local": False}]
    monkeypatch.setattr(operations.fleet_records, "records", lambda repo, machine_id: records)
    with urllib.request.urlopen(f"{server}/api/fleet", timeout=5) as response:
        payload = json.loads(response.read())
    assert payload == {"machine_id": "testmachine", "machines": [
        {**records[0], "remote_control": False}
    ]}


@pytest.mark.parametrize("path", ["/api/fleet", "/api/git?fetch=0"])
def test_git_and_fleet_are_busy_during_another_store_operation(server, path):
    with operations._serialized():
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{server}{path}", timeout=5)
    assert error.value.code == 423
    assert "store is busy" in json.loads(error.value.read())["error"]
