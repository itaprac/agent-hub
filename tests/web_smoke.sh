#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
TMP="$(CDPATH= cd -- "$TMP" && pwd -P)"
SERVER_PID=""
cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf -- "$TMP"
}
trap cleanup EXIT
trap 'cat "$TMP/web.log" >&2 || true' ERR

# All files, agent paths, Git configuration, and installer output stay in TMP.
export HOME="$TMP/home" PYTHONPATH="$ROOT" PYTHONUNBUFFERED=1
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
unset AGENT_HUB_STORE CLAUDE_CONFIG_DIR CODEX_HOME XDG_CONFIG_HOME XDG_STATE_HOME
unset AUTOHAND_HOME GROK_HOME HERMES_HOME VIBE_HOME
export PATH="$TMP/bin:$PATH"
mkdir -p "$HOME/.agents/skills/global-one" "$HOME/.config/agent-hub" "$TMP/bin"
printf 'testmachine\n' > "$HOME/.config/agent-hub/machine"
printf '# Global fixture\n' > "$HOME/.agents/skills/global-one/SKILL.md"
printf 'Global instructions\n' > "$HOME/.agents/AGENTS.md"
cat > "$HOME/.agents/hub.toml" <<'TOML'
[agents]
enabled = ["claude"]
[agents.claude]
skills_global = "~/.claude/skills"
instructions_global = "~/.claude/CLAUDE.md"
TOML
cat > "$TMP/bin/npx" <<'PY'
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
store = Path.home() / '.agents'
assert Path.cwd() == store
assert args in (
    ['-y', 'skills', 'add', 'example/skills', '-g', '-y', '--skill', 'installed-one'],
    ['-y', 'skills', 'update', '-g', 'installed-one'],
), args
with (Path.home() / 'npx-calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
skill = store / 'skills' / 'installed-one'
skill.mkdir(parents=True, exist_ok=True)
verb = 'Updated' if args[2] == 'update' else 'Installed'
(skill / 'SKILL.md').write_text(f'# {verb} fixture\n')
(store / '.skill-lock.json').write_text(json.dumps({
    'version': 3,
    'skills': {'installed-one': {
        'source': 'example/skills', 'sourceType': 'github',
        'sourceUrl': 'https://github.com/example/skills',
        'skillPath': 'skills/installed-one/SKILL.md',
        'installedAt': '2026-01-01T00:00:00Z',
        'updatedAt': '2026-01-02T00:00:00Z',
    }},
}))
PY
chmod +x "$TMP/bin/npx"
git -C "$HOME/.agents" init -q
git -C "$HOME/.agents" config user.name 'Console smoke'
git -C "$HOME/.agents" config user.email smoke@example.invalid
git -C "$HOME/.agents" add -A
git -C "$HOME/.agents" commit -qm fixture

python3 -m agenthub.webapp --store "$HOME/.agents" --host 127.0.0.1 --port 0 --quiet \
    > "$TMP/web.log" 2>&1 &
SERVER_PID=$!
python3 - "$TMP/web.log" <<'PY'
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

home = Path.home()
store = home / '.agents'
log = Path(sys.argv[1])
for _ in range(100):
    match = re.search(r'http://127\.0\.0\.1:\d+', log.read_text())
    if match:
        break
    time.sleep(0.1)
else:
    raise AssertionError('Console did not report a listening URL')
base = match.group()

def request(method, path, payload=None, expected=200, origin=True):
    headers = {'Origin': base, 'Sec-Fetch-Site': 'same-origin'} if origin else {}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        base + path, method=method, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read()
        allowed = expected if isinstance(expected, tuple) else (expected,)
        assert response.status in allowed, (method, path, response.status, body)
        if response.headers.get_content_type() == 'application/json':
            return json.loads(body)
        return body

print('Check Console assets, Store state, status, and Fleet')
assert b'<!doctype html>' in request('GET', '/')
assert request('GET', '/style.css')
assert request('GET', '/js/app.js')
state = request('GET', '/api/state')
assert state['machine_id'] == 'testmachine'
assert Path(state['repo']) == store
assert 'peers' not in state and 'token' not in state
assert [s['name'] for s in state['skills']['global']] == ['global-one']
assert [f['name'] for f in state['config_files']] == ['hub.toml']
status = request('GET', '/api/status')
assert status['exit_code'] == 1
assert any(line['level'] == 'MISSING' for line in status['lines'])
fleet = request('GET', '/api/fleet')
assert fleet['machine_id'] == 'testmachine' and fleet['machines'] == []
for path in ('/api/peers', '/api/peer/status', '/api/nope'):
    request('GET', path, expected=404)
request('POST', '/api/run', {'command': 'apply'}, expected=401, origin=False)

print('Check dry Apply, relative links, and Managed blocks')
assert request('POST', '/api/run', {'command': 'apply', 'dry_run': True})['exit_code'] == 0
link = home / '.claude/skills/global-one'
assert not link.exists()
assert request('POST', '/api/run', {'command': 'apply'})['exit_code'] == 0
assert link.is_symlink() and link.resolve() == store / 'skills/global-one'
assert not link.readlink().is_absolute()
assert 'Global instructions' in (home / '.claude/CLAUDE.md').read_text()
request('POST', '/api/run', {'command': 'unknown'}, expected=400)

print('Check file creation, revisions, deletion, TOML, and traversal')
payload = {'path': 'agents/claude.md', 'content': 'Overlay\nżółw\n', 'revision': None}
created = request('PUT', '/api/file', payload)
assert created['created'] is True
file = request('GET', '/api/file?path=agents/claude.md')
assert file['content'] == payload['content'] and len(file['revision']) == 64
request('PUT', '/api/file', {'path': payload['path'], 'content': 'missing'}, expected=428)
updated = request('PUT', '/api/file', {**payload, 'content': 'New overlay\n', 'revision': file['revision']})
request('PUT', '/api/file', {**payload, 'revision': file['revision']}, expected=409)
request('DELETE', '/api/file', {'path': payload['path'], 'revision': file['revision']}, expected=409)
assert (store / payload['path']).read_text() == 'New overlay\n'
request('DELETE', '/api/file', {'path': payload['path'], 'revision': updated['revision']})
assert not (store / payload['path']).exists()
config = request('GET', '/api/file?path=hub.toml')
request('PUT', '/api/file', {'path': 'hub.toml', 'content': '[broken', 'revision': config['revision']}, expected=422)
assert request('GET', '/api/file?path=hub.toml')['revision'] == config['revision']
for path in ('../../etc/passwd', '/etc/passwd', '.git/config', 'skills/../../secret', 'web.py'):
    request('GET', '/api/file?path=' + path, expected=(400, 403))
request('PUT', '/api/file', {'path': '../escaped.md', 'content': 'x', 'revision': None}, expected=(400, 403))
assert not (home / 'escaped.md').exists()

print('Check add Skill, stubbed skills.sh install/update, and provenance')
assert request('POST', '/api/add-skill', {'name': 'web-made'})['exit_code'] == 0
assert (store / 'skills/web-made/SKILL.md').exists()
assert request('POST', '/api/add-skill', {'name': 'web-made'})['exit_code'] == 1
installed = request('POST', '/api/run', {'command': 'install', 'source': 'example/skills', 'skill': 'installed-one'})
assert installed['exit_code'] == 0, installed
assert (home / '.claude/skills/installed-one').resolve() == store / 'skills/installed-one'
state = request('GET', '/api/state')
skill = next(s for s in state['skills']['global'] if s['name'] == 'installed-one')
assert skill['installed'] is True and skill['provenance']['source'] == 'example/skills'
updated = request('POST', '/api/run', {'command': 'update', 'names': ['installed-one']})
assert updated['exit_code'] == 0, updated
assert (store / 'skills/installed-one/SKILL.md').read_text() == '# Updated fixture\n'
assert len((home / 'npx-calls.jsonl').read_text().splitlines()) == 2
assert subprocess.check_output(['git', '-C', str(store), 'show', 'HEAD:skills/installed-one/SKILL.md']) == b'# Updated fixture\n'
assert subprocess.check_output(['git', '-C', str(store), 'show', 'HEAD:.skill-lock.json'])
assert request('GET', '/api/usage')['buckets'] == []
print('WEB SMOKE TEST PASSED')
PY
