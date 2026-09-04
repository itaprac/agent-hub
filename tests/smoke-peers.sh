#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
PID_A=""
PID_B=""

cleanup() {
    for pid in "$PID_A" "$PID_B"; do
        if [[ -n "$pid" ]]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    rm -rf -- "$TMP"
}
trap cleanup EXIT
trap 'echo "--- macmini log ---" >&2; cat "$TMP/a.log" >&2 2>/dev/null || true; echo "--- macbook log ---" >&2; cat "$TMP/b.log" >&2 2>/dev/null || true' ERR

read -r PORT_A PORT_B < <(python3 - <<'PY'
import socket

sockets = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sockets.append(sock)
print(*(sock.getsockname()[1] for sock in sockets))
for sock in sockets:
    sock.close()
PY
)

TOKEN="peer-smoke-token"
SEED="$TMP/seed"
BARE="$TMP/origin.git"
REPO_A="$TMP/macmini"
REPO_B="$TMP/macbook"
HOME_A="$TMP/home-a"
HOME_B="$TMP/home-b"
mkdir -p "$SEED" "$HOME_A" "$HOME_B"
cp -R "$ROOT/." "$SEED/"
rm -rf -- "$SEED/.git" "$SEED/__pycache__" "$SEED/.venv" "$SEED/.pytest_cache"

python3 - "$SEED" "$PORT_A" "$PORT_B" <<'PY'
from pathlib import Path
import sys

repo = Path(sys.argv[1])
port_a, port_b = sys.argv[2:]
(repo / "config").mkdir(parents=True, exist_ok=True)
(repo / "hub.toml").write_text("[agents]\nenabled = []\n", encoding="utf-8")
(repo / "config" / "hub.toml").write_text(
    '[machines]\n"fixture-a" = "macmini"\n"fixture-b" = "macbook"\n',
    encoding="utf-8",
)
(repo / "config" / "agents.toml").write_text("", encoding="utf-8")
(repo / "config" / "projects.toml").write_text("", encoding="utf-8")
(repo / "config" / "skills.toml").write_text("", encoding="utf-8")
(repo / "config" / "peers.toml").write_text(
    '[urls]\n'
    f'macmini = "http://127.0.0.1:{port_a}"\n'
    f'macbook = "http://127.0.0.1:{port_b}"\n',
    encoding="utf-8",
)
PY

git -C "$SEED" init -q -b main
git -C "$SEED" config user.name "agent-hub peers smoke"
git -C "$SEED" config user.email "smoke@example.invalid"
git -C "$SEED" add -A
git -C "$SEED" commit -qm "fixture"
git clone -q --bare "$SEED" "$BARE"
git clone -q "$BARE" "$REPO_A"
git clone -q "$BARE" "$REPO_B"
for repo in "$REPO_A" "$REPO_B"; do
    git -C "$repo" config user.name "agent-hub peers smoke"
    git -C "$repo" config user.email "smoke@example.invalid"
done

mkdir -p "$HOME_A/.config/agent-hub" "$HOME_B/.config/agent-hub"
printf 'macmini\n' > "$HOME_A/.config/agent-hub/machine"
printf 'macbook\n' > "$HOME_B/.config/agent-hub/machine"

HOME="$HOME_A" AGENT_HUB_MACHINE=macmini AGENT_HUB_PEER_TOKEN="$TOKEN" \
    python3 "$REPO_A/web.py" --repo "$REPO_A" --host 127.0.0.1 --port "$PORT_A" --quiet \
    >"$TMP/a.log" 2>&1 &
PID_A=$!
HOME="$HOME_B" AGENT_HUB_MACHINE=macbook AGENT_HUB_PEER_TOKEN="$TOKEN" \
    python3 "$REPO_B/web.py" --repo "$REPO_B" --host 127.0.0.1 --port "$PORT_B" --quiet \
    >"$TMP/b.log" 2>&1 &
PID_B=$!

BASE_A="http://127.0.0.1:$PORT_A"
BASE_B="http://127.0.0.1:$PORT_B"
for base in "$BASE_A" "$BASE_B"; do
    ready=0
    for _ in $(seq 1 100); do
        if curl -fsS -o /dev/null "$base/api/state" 2>/dev/null; then
            ready=1
            break
        fi
        sleep 0.1
    done
    test "$ready" -eq 1
done

BODY="$TMP/body.json"
json_assert() {
    local expression="$1"
    python3 - "$BODY" "$expression" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if not eval(sys.argv[2], {"data": data}):
    raise SystemExit(f"assertion failed: {sys.argv[2]}\nresponse: {data!r}")
PY
}

echo "== 1. /api/peers sees both machines online and synchronized =="
curl -fsS "$BASE_A/api/peers" >"$BODY"
json_assert 'data["self"] == "macmini"'
json_assert 'data["in_sync"] is True'
json_assert 'len(data["machines"]) == 2'
json_assert 'all(machine["online"] for machine in data["machines"])'
json_assert '{machine["machine"] for machine in data["machines"]} == {"macmini", "macbook"}'
echo "PASS"

echo "== 2. a commit on one machine makes the federation diverge =="
printf 'only on macbook\n' >"$REPO_B/diverged.txt"
git -C "$REPO_B" add diverged.txt
git -C "$REPO_B" commit -qm "diverge macbook"
curl -fsS "$BASE_A/api/peers" >"$BODY"
json_assert 'data["in_sync"] is False'
json_assert 'next(machine for machine in data["machines"] if machine["machine"] == "macbook")["git"]["ahead"] == 1'
echo "PASS"

echo "== 3. remote run is proxied and bad auth is rejected =="
HTTP_CODE="$(curl -sS -o "$BODY" -w '%{http_code}' \
    -H 'Content-Type: application/json' -H "Origin: $BASE_A" \
    -H 'Sec-Fetch-Site: same-origin' \
    --data '{"command":"apply","dry_run":true}' \
    "$BASE_A/api/peers/macbook/run")"
test "$HTTP_CODE" = "200"
json_assert 'data["exit_code"] == 0'

HTTP_CODE="$(curl -sS -o "$BODY" -w '%{http_code}' \
    -H 'Content-Type: application/json' -H 'X-Hub-Token: wrong' \
    --data '{"command":"apply","dry_run":true}' \
    "$BASE_B/api/run")"
test "$HTTP_CODE" = "401"
json_assert 'data == {"error": "authentication required"}'
echo "PASS"

echo "PEERS SMOKE TEST PASSED"
