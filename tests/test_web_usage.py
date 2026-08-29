"""HTTP contract for the server-computed Usage report."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def get(base: str, route: str) -> dict:
    with urllib.request.urlopen(f"{base}{route}", timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_usage_returns_finished_rollups_and_server_defaults(server: str, home: Path) -> None:
    transcript = home / ".claude" / "projects" / "demo" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sessionId": "session-1",
                "costUSD": 1.5,
                "message": {
                    "id": "message-1",
                    "model": "claude-test",
                    "usage": {
                        "input_tokens": 5,
                        "cache_read_input_tokens": 7,
                        "cache_creation_input_tokens": 2,
                        "output_tokens": 3,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rates = home / ".cache" / "agent-hub" / "usage-model-rates.json"
    rates.parent.mkdir(parents=True)
    rates.write_text(
        json.dumps(
            {
                "fetchedAt": time.time(),
                "document": {
                    "claude-test": {
                        "input_cost_per_token": 1e-6,
                        "output_cost_per_token": 2e-6,
                        "cache_read_input_token_cost": 1e-7,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    payload = get(server, "/api/usage?days=7&tz=UTC")

    assert payload["timeZone"] == "UTC"
    assert payload["settings"] == {
        "claude": True,
        "codex": True,
        "grok": False,
        "cursor": False,
        "cursorTokenSet": False,
    }
    assert payload["rollups"]["total"]["totalTokens"] == 17
    assert payload["rollups"]["total"]["costUsd"] == 1.5
    assert payload["rollups"]["byModel"][0]["model"] == "claude-test"
    assert [row["source"] for row in payload["rollups"]["bySource"]] == [
        "claude",
        "codex",
    ]
