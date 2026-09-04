#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf -- "$TMP"
}
trap cleanup EXIT
trap 'echo "--- server log ---" >&2; cat "$TMP/web.log" >&2 || true' ERR

REPO="$TMP/repo"
FAKE_HOME="$TMP/home"
PROJECT="$TMP/project"
mkdir -p "$FAKE_HOME" "$PROJECT"
cp -R "$ROOT" "$REPO"
rm -rf -- "$REPO/.git" "$REPO/__pycache__" "$REPO/.venv" "$REPO/.pytest_cache"

REPO="$(CDPATH= cd -- "$REPO" && pwd -P)"
FAKE_HOME="$(CDPATH= cd -- "$FAKE_HOME" && pwd -P)"
PROJECT="$(CDPATH= cd -- "$PROJECT" && pwd -P)"

# Every hub.py invocation below runs with this fake HOME; the real home
# directory of the user is never touched.
export HOME="$FAKE_HOME"
HOSTNAME="$(python3 -c 'import platform; print(platform.node())')"

python3 - "$REPO" "$PROJECT" "$HOSTNAME" <<'PY'
from pathlib import Path
import shutil
import sys

repo = Path(sys.argv[1])
project = Path(sys.argv[2])
hostname = sys.argv[3]

# The fixture starts from a copy of the real repo; drop its content trees so
# assertions see only fixture data regardless of what the repo accumulates.
for leftover in ("skills", "instructions", "agents", "projects", ".claude", ".agents"):
    shutil.rmtree(repo / leftover, ignore_errors=True)

(repo / "config").mkdir(parents=True, exist_ok=True)
(repo / "hub.toml").write_text(
    """[agents]
enabled = ["claude"]
mode = "symlink"

[agents.claude]
skills_global = "~/.claude/skills"
instructions_global = "~/.claude/CLAUDE.md"
""",
    encoding="utf-8",
)
pin = Path.home() / ".config" / "agent-hub" / "machine"
pin.parent.mkdir(parents=True)
pin.write_text("testmachine\n", encoding="utf-8")
(repo / "config" / "skills.toml").write_text("", encoding="utf-8")

files = {
    repo / "skills" / "global-one" / "SKILL.md": "# Global fixture\n",
    repo / "skills" / "global-one" / "check.sh": "#!/bin/sh\necho before\n",
    repo / "projects" / "demo" / "skills" / "project-one" / "SKILL.md": "# Project fixture\n",
    repo / "AGENTS.md": "Global base v1\n",
}
for path, content in files.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
(repo / "skills" / "global-one" / "check.sh").chmod(0o755)
PY

git -C "$REPO" init -q
git -C "$REPO" config user.name "agent-hub web smoke"
git -C "$REPO" config user.email "smoke@example.invalid"
git -C "$REPO" add -A
git -C "$REPO" commit -qm "fixture"

python3 "$REPO/web.py" --repo "$REPO" --host 127.0.0.1 --port 0 --quiet \
    >"$TMP/web.log" 2>&1 &
SERVER_PID=$!

BASE=""
for _ in $(seq 1 100); do
    if BASE="$(grep -o 'http://127\.0\.0\.1:[0-9]*' "$TMP/web.log" 2>/dev/null | head -n 1)" && [[ -n "$BASE" ]]; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ASSERTION FAILED: web.py exited during startup" >&2
        cat "$TMP/web.log" >&2
        exit 1
    fi
    sleep 0.1
done
if [[ -z "$BASE" ]]; then
    echo "ASSERTION FAILED: web.py did not report a listening URL" >&2
    cat "$TMP/web.log" >&2
    exit 1
fi

READY=0
for _ in $(seq 1 100); do
    if curl -fsS -o /dev/null "$BASE/api/state" 2>/dev/null; then
        READY=1
        break
    fi
    sleep 0.1
done
test "$READY" -eq 1

BODY="$TMP/body"

request() { # method path [json-body-file] -> prints HTTP status, body in $BODY
    local method="$1" path="$2" data="${3:-}"
    if [[ -n "$data" ]]; then
        curl -sS -X "$method" -H "Content-Type: application/json" \
            -H "Origin: $BASE" -H "Sec-Fetch-Site: same-origin" --data-binary @"$data" \
            -o "$BODY" -w '%{http_code}' "$BASE$path"
    else
        curl -sS -X "$method" -H "Origin: $BASE" -H "Sec-Fetch-Site: same-origin" \
            -o "$BODY" -w '%{http_code}' "$BASE$path"
    fi
}

expect_status() {
    local expected="$1" actual="$2" label="$3"
    if [[ "$actual" != "$expected" ]]; then
        echo "ASSERTION FAILED: $label expected HTTP $expected, got $actual" >&2
        cat "$BODY" >&2
        exit 1
    fi
}

expect_body() {
    local text="$1" label="$2"
    if ! grep -Fq -- "$text" "$BODY"; then
        echo "ASSERTION FAILED: $label response does not contain '$text'" >&2
        cat "$BODY" >&2
        exit 1
    fi
}

json() { # python expression evaluated against the parsed body as `data`
    python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$BODY" "$1"
}

echo "== 1. GET / serves the HTML shell =="
CODE="$(request GET /)"
expect_status 200 "$CODE" "GET /"
expect_body "<!doctype html>" "GET /"
expect_body "/js/app.js" "GET /"
CODE="$(request GET /style.css)"
expect_status 200 "$CODE" "GET /style.css"
CODE="$(request GET /js/app.js)"
expect_status 200 "$CODE" "GET /js/app.js"
echo "PASS"

echo "== 2. GET /api/state describes the fixture repository =="
CODE="$(request GET /api/state)"
expect_status 200 "$CODE" "GET /api/state"
test "$(json 'data["machine_id"]')" = "testmachine"
test "$(json 'data["repo"]')" = "$REPO"
test "$(json '"token" not in data')" = "True"
test "$(json '[s["name"] for s in data["skills"]["global"]]')" = "['global-one']"
test "$(json 'data["skills"]["projects"]')" = "{}"
test "$(json '[a["name"] for a in data["agents"]]')" = "['claude']"
test "$(json 'data["projects"]')" = "[]"
test "$(json '[i["name"] for i in data["instructions"]["global"]]')" = "['AGENTS.md']"
test "$(json '[c["name"] for c in data["config_files"]][:2]')" = "['hub.toml']"
echo "PASS"

echo "== 3. GET /api/status returns an exit code and parsed lines =="
CODE="$(request GET /api/status)"
expect_status 200 "$CODE" "GET /api/status"
test "$(json 'data["exit_code"]')" = "1"
test "$(json 'len(data["lines"]) > 0')" = "True"
test "$(json '"MISSING" in {l["level"] for l in data["lines"]}')" = "True"
test "$(json 'all(set(l) == {"level", "text"} for l in data["lines"])')" = "True"
echo "PASS"

echo "== 3b. repository contention fails immediately =="
python3 - "$REPO" <<'PY'
from pathlib import Path
import sys

import threading

from agenthub import operations

repo = Path(sys.argv[1])
content_operations = operations.ContentOperations(repo)
entered = threading.Event()
release = threading.Event()
original = operations.config.load_machine_projection

def load(path):
    entered.set()
    assert release.wait(timeout=5)
    return original(path)

operations.config.load_machine_projection = load
thread = threading.Thread(target=content_operations.status)
thread.start()
assert entered.wait(timeout=5)
try:
    for operation in (
        content_operations.status,
        lambda: content_operations.write_file("hub.toml", "", None),
        lambda: content_operations.delete_file("hub.toml", None),
    ):
        try:
            operation()
        except operations.RepositoryBusyError as exc:
            assert str(exc) == "store is busy; try again after the current operation finishes"
        else:
            raise AssertionError("repository operation waited for or bypassed the held lock")
finally:
    release.set()
    thread.join(timeout=5)
assert not thread.is_alive()
PY
echo "PASS"

echo "== 4. POST /api/run apply with dry_run changes nothing =="
printf '{"command": "apply", "dry_run": true}' >"$TMP/run.json"
CODE="$(request POST /api/run "$TMP/run.json")"
expect_status 200 "$CODE" "POST /api/run (dry run)"
test "$(json 'data["exit_code"]')" = "0"
test "$(json '"link" in {l["level"] for l in data["lines"]}')" = "True"
test ! -e "$FAKE_HOME/.claude/skills/global-one"

echo "== 4b. POST /api/run apply deploys into the fake HOME =="
printf '{"command": "apply", "dry_run": false}' >"$TMP/run.json"
CODE="$(request POST /api/run "$TMP/run.json")"
expect_status 200 "$CODE" "POST /api/run (apply)"
test "$(json 'data["exit_code"]')" = "0"
test -L "$FAKE_HOME/.claude/skills/global-one"
test "$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$FAKE_HOME/.claude/skills/global-one")" = "$REPO/skills/global-one"
grep -Fq "Global base v1" "$FAKE_HOME/.claude/CLAUDE.md"
printf '{"command": "rm -rf", "dry_run": false}' >"$TMP/run.json"
CODE="$(request POST /api/run "$TMP/run.json")"
expect_status 400 "$CODE" "POST /api/run (bad command)"
echo "PASS"

echo "== 5. PUT and GET /api/file round-trip inside the repository =="
python3 - "$TMP/file.json" <<'PY'
import json, sys
payload = {
    "path": "agents/claude.md",
    "content": "Overlay written by the web UI\nżółw\n",
    "revision": None,
}
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(payload))
PY
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "PUT /api/file"
test "$(json 'data["created"]')" = "True"
test -f "$REPO/agents/claude.md"
CODE="$(request GET "/api/file?path=agents/claude.md")"
expect_status 200 "$CODE" "GET /api/file"
test "$(json 'data["content"]')" = "$(printf 'Overlay written by the web UI\n\xc5\xbc\xc3\xb3\xc5\x82w')"
REVISION="$(json 'data["revision"]')"
test "${#REVISION}" -eq 64
curl -sSI "$BASE/api/file?path=agents/claude.md" | grep -Fqi "etag: \"$REVISION\""

echo "== 5b. nested directories are created on demand =="
printf '{"path": "skills/global-one/nested/note.md", "content": "x\\n", "revision": null}' >"$TMP/file.json"
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "PUT /api/file (nested)"
test -f "$REPO/skills/global-one/nested/note.md"
REVISION="$(json 'data["revision"]')"
printf '{"path": "skills/global-one/nested/note.md", "revision": "%s"}' "$REVISION" >"$TMP/file.json"
CODE="$(request DELETE /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "DELETE /api/file"
test ! -e "$REPO/skills/global-one/nested/note.md"

echo "== 5c. stale and concurrent writes are rejected =="
printf '{"path": "agents/claude.md", "content": "missing revision\\n"}' >"$TMP/file.json"
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 428 "$CODE" "PUT /api/file (missing revision)"

CODE="$(request GET "/api/file?path=agents/claude.md")"
expect_status 200 "$CODE" "GET /api/file (before conflict)"
REVISION="$(json 'data["revision"]')"
python3 - "$TMP/write-a.json" "$TMP/write-b.json" "$REVISION" <<'PY'
import json, sys
for target, content in zip(sys.argv[1:3], ("writer A\n", "writer B\n")):
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"path": "agents/claude.md", "content": content, "revision": sys.argv[3]}, handle)
PY
curl -sS -X PUT -H "Content-Type: application/json" -H "Origin: $BASE" -H "Sec-Fetch-Site: same-origin" \
    --data-binary @"$TMP/write-a.json" -o "$TMP/write-a.body" -w '%{http_code}' "$BASE/api/file" >"$TMP/write-a.code" &
PID_A=$!
curl -sS -X PUT -H "Content-Type: application/json" -H "Origin: $BASE" -H "Sec-Fetch-Site: same-origin" \
    --data-binary @"$TMP/write-b.json" -o "$TMP/write-b.body" -w '%{http_code}' "$BASE/api/file" >"$TMP/write-b.code" &
PID_B=$!
wait "$PID_A"
wait "$PID_B"
CODES="$(sort "$TMP/write-a.code" "$TMP/write-b.code" | tr '\n' ' ')"
case "$CODES" in
    "200 409 "|"200 423 ") ;;
    *)
        echo "ASSERTION FAILED: concurrent writes returned HTTP $CODES" >&2
        exit 1
        ;;
esac
test "$(cat "$REPO/agents/claude.md")" = "writer A" -o \
    "$(cat "$REPO/agents/claude.md")" = "writer B"
test -z "$(find "$REPO/agents" -name '.claude.md.*' -print -quit)"

CODE="$(request GET "/api/file?path=agents/claude.md")"
expect_status 200 "$CODE" "GET /api/file (before stale delete)"
REVISION="$(json 'data["revision"]')"
python3 - "$TMP/file.json" "$REVISION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"path": "agents/claude.md", "content": "newer version\n", "revision": sys.argv[2]}, handle)
PY
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "PUT /api/file (before stale delete)"
printf '{"path": "agents/claude.md", "revision": "%s"}' "$REVISION" >"$TMP/file.json"
CODE="$(request DELETE /api/file "$TMP/file.json")"
expect_status 409 "$CODE" "DELETE /api/file (stale revision)"
test "$(cat "$REPO/agents/claude.md")" = "newer version"

echo "== 5d. atomic writes preserve executable permissions =="
CODE="$(request GET "/api/file?path=skills/global-one/check.sh")"
expect_status 200 "$CODE" "GET /api/file (executable)"
REVISION="$(json 'data["revision"]')"
python3 - "$TMP/file.json" "$REVISION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"path": "skills/global-one/check.sh", "content": "#!/bin/sh\necho after\n", "revision": sys.argv[2]}, handle)
PY
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "PUT /api/file (executable)"
test -x "$REPO/skills/global-one/check.sh"
test -z "$(find "$REPO/skills/global-one" -name '.check.sh.*' -print -quit)"

echo "== 5e. invalid TOML is rejected without changing the file =="
CODE="$(request GET "/api/file?path=hub.toml")"
expect_status 200 "$CODE" "GET /api/file (config before validation)"
REVISION="$(json 'data["revision"]')"
python3 - "$TMP/file.json" "$REVISION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"path": "hub.toml", "content": "[broken\n", "revision": sys.argv[2]}, handle)
PY
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 422 "$CODE" "PUT /api/file (invalid TOML)"
expect_body "invalid TOML" "PUT /api/file (invalid TOML)"
expect_body "line 1" "PUT /api/file (invalid TOML location)"
CODE="$(request GET "/api/file?path=hub.toml")"
expect_status 200 "$CODE" "GET /api/file (config after rejected validation)"
test "$(json 'data["revision"]')" = "$REVISION"
test "$(json 'data["content"].startswith("[agents]")')" = "True"

python3 - "$TMP/file.json" "$REVISION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"path": "hub.toml", "content": "[agents]\nenabled = [\"claude\"]\n\n[agents.claude]\nskills_global = \"~/.claude/skills\"\ninstructions_global = \"~/.claude/CLAUDE.md\"\n", "revision": sys.argv[2]}, handle)
PY
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 200 "$CODE" "PUT /api/file (valid TOML)"

echo "== 5f. traversal and unsupported files are rejected =="
CODE="$(request GET "/api/file?path=../../etc/passwd")"
test "${CODE:0:1}" = "4"
expect_body "error" "GET /api/file (traversal)"
CODE="$(request GET "/api/file?path=/etc/passwd")"
test "${CODE:0:1}" = "4"
CODE="$(request GET "/api/file?path=skills/global-one/../../../../hub.py")"
test "${CODE:0:1}" = "4"
CODE="$(request GET "/api/file?path=skills/../../home/.claude/CLAUDE.md")"
test "${CODE:0:1}" = "4"
CODE="$(request GET "/api/file?path=.git/config")"
test "${CODE:0:1}" = "4"
CODE="$(request GET "/api/file?path=README.pdf")"
test "${CODE:0:1}" = "4"
CODE="$(request GET "/api/file?path=hub.py")"
expect_status 403 "$CODE" "GET /api/file (application source)"
printf '%s' '{"path": "web.py", "content": "not allowed\n", "revision": null}' >"$TMP/file.json"
CODE="$(request PUT /api/file "$TMP/file.json")"
expect_status 403 "$CODE" "PUT /api/file (application source)"
grep -Fq 'Compatibility entry point' "$REPO/web.py"
test ! -e "$REPO/../etc"
echo "PASS"

echo "== 6. POST /api/add-skill creates a skill in the fixture repository =="
printf '{"name": "web-made"}' >"$TMP/skill.json"
CODE="$(request POST /api/add-skill "$TMP/skill.json")"
expect_status 200 "$CODE" "POST /api/add-skill"
test "$(json 'data["exit_code"]')" = "0"
grep -Fq "name: web-made" "$REPO/skills/web-made/SKILL.md"

printf '{"name": "web-made-project", "project": "demo"}' >"$TMP/skill.json"
CODE="$(request POST /api/add-skill "$TMP/skill.json")"
expect_status 200 "$CODE" "POST /api/add-skill (project)"
test "$(json 'data["exit_code"]')" = "1"
test ! -e "$REPO/projects/demo/skills/web-made-project"

printf '{"name": "web-made"}' >"$TMP/skill.json"
CODE="$(request POST /api/add-skill "$TMP/skill.json")"
expect_status 200 "$CODE" "POST /api/add-skill (duplicate)"
test "$(json 'data["exit_code"]')" = "1"
test "$(json '"ERROR" in {l["level"] for l in data["lines"]}')" = "True"

CODE="$(request GET /api/state)"
test "$(json '[s["name"] for s in data["skills"]["global"]]')" = "['global-one', 'web-made']"
echo "PASS"

echo "== 7. POST /api/adopt imports a directory from the fake HOME =="
mkdir -p "$FAKE_HOME/.claude/skills/adopted"
printf '# Adopted by the web UI\n' >"$FAKE_HOME/.claude/skills/adopted/SKILL.md"
python3 - "$TMP/adopt.json" "$FAKE_HOME/.claude/skills/adopted" <<'PY'
import json, sys
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({"path": sys.argv[2]}))
PY
CODE="$(request POST /api/adopt "$TMP/adopt.json")"
expect_status 200 "$CODE" "POST /api/adopt"
test "$(json 'data["exit_code"]')" = "0"
test -f "$REPO/skills/adopted/SKILL.md"
test -L "$FAKE_HOME/.claude/skills/adopted"
echo "PASS"

echo "== 7b. usage settings stay in the fake HOME =="
CODE="$(request GET /api/usage/settings)"
expect_status 200 "$CODE" "GET /api/usage/settings"
test "$(json 'data["claude"]')" = "True"
test "$(json 'data["codex"]')" = "True"
test "$(json 'data["grok"]')" = "False"
test "$(json 'data["cursor"]')" = "False"
test "$(json 'data["cursorTokenSet"]')" = "False"
test "$(json '"cursorToken" not in data')" = "True"
printf '{"grok": true, "cursor": true, "cursorToken": "smoke-token"}' >"$TMP/usage-settings.json"
CODE="$(request PUT /api/usage/settings "$TMP/usage-settings.json")"
expect_status 200 "$CODE" "PUT /api/usage/settings"
test "$(json 'data["grok"]')" = "True"
test "$(json 'data["cursorTokenSet"]')" = "True"
test "$(json '"cursorToken" not in data')" = "True"
TOKEN_FILE="$FAKE_HOME/.config/agent-hub/cursor-session-token"
test -f "$TOKEN_FILE"
test "$(python3 -c 'import os,stat,sys; print(stat.S_IMODE(os.stat(sys.argv[1]).st_mode))' "$TOKEN_FILE")" = "384"
grep -Fxq "smoke-token" "$TOKEN_FILE"
printf '{"grok": false, "cursorToken": 42}' >"$TMP/usage-settings.json"
CODE="$(request PUT /api/usage/settings "$TMP/usage-settings.json")"
expect_status 400 "$CODE" "PUT /api/usage/settings (invalid token type)"
CODE="$(request GET /api/usage/settings)"
expect_status 200 "$CODE" "GET /api/usage/settings after invalid patch"
test "$(json 'data["grok"]')" = "True"
test "$(json 'data["cursorTokenSet"]')" = "True"
printf '{"grok": "false"}' >"$TMP/usage-settings.json"
CODE="$(request PUT /api/usage/settings "$TMP/usage-settings.json")"
expect_status 400 "$CODE" "PUT /api/usage/settings (invalid source flag)"
printf '{"cursorToken": ""}' >"$TMP/usage-settings.json"
CODE="$(request PUT /api/usage/settings "$TMP/usage-settings.json")"
expect_status 200 "$CODE" "PUT /api/usage/settings (clear token)"
test "$(json 'data["cursorTokenSet"]')" = "False"
test ! -e "$TOKEN_FILE"
CODE="$(request GET /api/usage)"
expect_status 200 "$CODE" "GET /api/usage"
test "$(json '"buckets" in data')" = "True"
echo "PASS"

echo "== 8. responses are uncacheable and unknown endpoints 404 =="
curl -sSI "$BASE/api/state" | grep -Fqi "cache-control: no-store"
curl -sSI "$BASE/" | grep -Fqi "cache-control: no-store"
curl -sSI "$BASE/" | grep -Fqi "content-security-policy:"
if grep -Eq '<(link|script|img)[^>]+(href|src)="https?://' "$REPO/web/index.html"; then
    echo "ASSERTION FAILED: frontend references an external resource" >&2
    exit 1
fi
CODE="$(request GET /api/nope)"
expect_status 404 "$CODE" "GET /api/nope"
CODE="$(request GET /../web.py)"
expect_status 404 "$CODE" "GET /../web.py"
CODE="$(request DELETE /api/state)"
expect_status 405 "$CODE" "DELETE /api/state"
echo "PASS"

echo "== 8b. unexpected errors are logged but not exposed =="
python3 - <<'PY'
import contextlib
import io

from agenthub import webapp

secret = "sensitive exception detail"

class ProbeHandler(webapp.Handler):
    def handle_route(self, method):
        raise RuntimeError(secret)

    def send_error_json(self, status, message):
        self.result = (status, message)

handler = object.__new__(ProbeHandler)
handler.path = "/api/probe"
log = io.StringIO()
with contextlib.redirect_stderr(log):
    handler.dispatch("GET")

assert handler.result == (500, "internal server error")
assert secret not in handler.result[1]
assert secret in log.getvalue()
PY
echo "PASS"

echo "== 9. browser origin checks cannot be bypassed by retired peer tokens =="
printf '{"command": "apply", "dry_run": true}' >"$TMP/run.json"
CODE="$(curl -sS -D "$TMP/unauth.headers" -o "$BODY" -w '%{http_code}' -H 'Content-Type: application/json' \
    --data-binary @"$TMP/run.json" "$BASE/api/run")"
expect_status 401 "$CODE" "POST /api/run (no authentication)"
grep -Fqi 'connection: close' "$TMP/unauth.headers"
CODE="$(curl -sS -o "$BODY" -w '%{http_code}' -H 'Content-Type: application/json' \
    -H 'X-Hub-Token: web-smoke-token' --data-binary @"$TMP/run.json" "$BASE/api/run")"
expect_status 401 "$CODE" "POST /api/run (retired peer token)"
printf '{"path": "hub.toml", "content": ""}' >"$TMP/file.json"
CODE="$(curl -sS -X PUT -o "$BODY" -w '%{http_code}' -H 'Content-Type: application/json' \
    -H 'X-Hub-Token: web-smoke-token' --data-binary @"$TMP/file.json" "$BASE/api/file")"
expect_status 401 "$CODE" "PUT /api/file (peer token only)"
echo "PASS"

echo "== 10. the real HOME was never touched =="
test "$HOME" = "$FAKE_HOME"
echo "PASS"

echo "WEB SMOKE TEST PASSED"
