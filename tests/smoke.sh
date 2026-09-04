#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
TMP="$(CDPATH= cd -- "$TMP" && pwd -P)"
export HOME="$TMP/home"
export PYTHONPATH="$ROOT"
unset AGENT_HUB_STORE AGENT_HUB_REPO AGENT_HUB_MACHINE CLAUDE_CONFIG_DIR CODEX_HOME XDG_CONFIG_HOME
unset AUTOHAND_HOME GROK_HOME HERMES_HOME VIBE_HOME
export GIT_AUTHOR_NAME="agent-hub smoke"
export GIT_AUTHOR_EMAIL="smoke@example.invalid"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
STORE="$HOME/.agents"
mkdir -p "$HOME/.config/agent-hub"
printf 'first-machine\n' > "$HOME/.config/agent-hub/machine"
mkdir -p "$STORE" "$HOME/.claude/skills/claude-local" "$HOME/.cursor/skills/cursor-local"
printf '# Claude local\n' > "$HOME/.claude/skills/claude-local/SKILL.md"
printf '# Cursor local\n' > "$HOME/.cursor/skills/cursor-local/SKILL.md"
printf 'Global base\n' > "$STORE/AGENTS.md"
cat > "$STORE/hub.toml" <<'EOF'
[agents]
enabled = ["claude-code", "cursor"]
EOF

hub() { python3 -m agenthub.cli "$@"; }
assert_link() {
    python3 - "$1" "$2" <<'PY'
import os
from pathlib import Path
import sys
link, destination = map(Path, sys.argv[1:])
assert link.is_symlink(), link
assert link.resolve() == destination.resolve(), (link, destination)
assert not os.path.isabs(os.readlink(link)), link
PY
}

echo "== init adopts Skills from two detected Agents =="
hub init --yes
assert_link "$HOME/.claude/skills/claude-local" "$STORE/skills/claude-local"
assert_link "$HOME/.cursor/skills/cursor-local" "$STORE/skills/cursor-local"
test -d "$STORE/.git"

# Init does not replace Store content on a second run.
BEFORE="$(git -C "$STORE" rev-parse HEAD)"
hub init --yes
test "$(git -C "$STORE" rev-parse HEAD)" = "$BEFORE"

echo "== apply renders instructions and creates relative links =="
mkdir -p "$STORE/skills/new-skill" "$STORE/agents"
printf '# New Skill\n' > "$STORE/skills/new-skill/SKILL.md"
printf 'Claude overlay\n' > "$STORE/agents/claude-code.md"
printf 'My notes\n' > "$HOME/.claude/CLAUDE.md"
hub --dry-run apply
test ! -e "$HOME/.claude/skills/new-skill"
hub apply
assert_link "$HOME/.claude/skills/new-skill" "$STORE/skills/new-skill"
assert_link "$HOME/.cursor/skills/new-skill" "$STORE/skills/new-skill"
grep -Fq 'My notes' "$HOME/.claude/CLAUDE.md"
grep -Fq 'Global base' "$HOME/.claude/CLAUDE.md"
grep -Fq 'Claude overlay' "$HOME/.claude/CLAUDE.md"
grep -Fq 'Edit ~/.agents/AGENTS.md' "$HOME/.claude/CLAUDE.md"
cp "$HOME/.claude/CLAUDE.md" "$TMP/instructions"
hub apply
cmp "$HOME/.claude/CLAUDE.md" "$TMP/instructions"

echo "== real directories and foreign links are preserved as drift =="
rm "$HOME/.claude/skills/new-skill"
mkdir -p "$HOME/.claude/skills/new-skill"
printf 'keep\n' > "$HOME/.claude/skills/new-skill/keep.txt"
if hub apply > "$TMP/drift" 2>&1; then exit 1; fi
grep -Fq '[DRIFT]' "$TMP/drift"
test "$(cat "$HOME/.claude/skills/new-skill/keep.txt")" = keep
rm -r "$HOME/.claude/skills/new-skill"
mkdir "$TMP/foreign"
ln -s "$TMP/foreign" "$HOME/.claude/skills/new-skill"
if hub apply > "$TMP/drift" 2>&1; then exit 1; fi
grep -Fq '[DRIFT]' "$TMP/drift"
test "$(readlink "$HOME/.claude/skills/new-skill")" = "$TMP/foreign"
rm "$HOME/.claude/skills/new-skill"
hub apply

echo "== stale content is repaired and malformed markers stay untouched =="
printf 'Updated instructions\n' > "$STORE/AGENTS.md"
if hub status > "$TMP/status" 2>&1; then exit 1; fi
grep -Fq '[STALE]' "$TMP/status"
hub apply
grep -Fq 'Updated instructions' "$HOME/.claude/CLAUDE.md"
cp "$HOME/.claude/CLAUDE.md" "$TMP/valid"
printf 'prefix <!-- agent-hub:begin -->\nold\n<!-- agent-hub:end -->\n' > "$HOME/.claude/CLAUDE.md"
cp "$HOME/.claude/CLAUDE.md" "$TMP/malformed"
if hub apply > "$TMP/drift" 2>&1; then exit 1; fi
grep -Fq '[DRIFT]' "$TMP/drift"
cmp "$HOME/.claude/CLAUDE.md" "$TMP/malformed"
cp "$TMP/valid" "$HOME/.claude/CLAUDE.md"

echo "== pruning removes only stale Store links =="
ln -s "$TMP/foreign" "$HOME/.claude/skills/foreign"
rm -r "$STORE/skills/new-skill"
hub --dry-run apply
test -L "$HOME/.claude/skills/new-skill"
hub apply
test ! -L "$HOME/.claude/skills/new-skill"
test -L "$HOME/.claude/skills/foreign"

echo "== add-skill and adopt use the Store skills directory =="
hub add-skill authored
grep -Fq 'name: authored' "$STORE/skills/authored/SKILL.md"
mkdir -p "$HOME/local-import"
printf '# Import\n' > "$HOME/local-import/SKILL.md"
hub adopt "$HOME/local-import" --name imported
assert_link "$HOME/local-import" "$STORE/skills/imported"
hub apply
assert_link "$HOME/.claude/skills/imported" "$STORE/skills/imported"

echo "== copy mode removes extras without following target symlinks =="
COPY_HOME="$TMP/copy-home"
mkdir -p "$COPY_HOME/.claude" "$COPY_HOME/.cursor"
HOME="$COPY_HOME" hub --store "$STORE" apply --copy
test -f "$COPY_HOME/.claude/skills/imported/SKILL.md"
test ! -L "$COPY_HOME/.claude/skills/imported"
printf 'keep external\n' > "$TMP/foreign/keep.txt"
ln -s "$TMP/foreign" "$COPY_HOME/.claude/skills/imported/extra"
HOME="$COPY_HOME" hub --store "$STORE" apply --copy
test ! -e "$COPY_HOME/.claude/skills/imported/extra"
test "$(cat "$TMP/foreign/keep.txt")" = 'keep external'

echo "== Sync shares Machine records through a bare origin =="
ORIGIN="$TMP/origin.git"
git init -q --bare -b main "$ORIGIN"
git -C "$STORE" remote add origin "$ORIGIN"
git -C "$STORE" push -q -u origin main
hub sync

echo "== A second Machine clones the Store and joins the Fleet =="
SECOND_HOME="$TMP/second-home"
mkdir -p "$SECOND_HOME/.config/agent-hub" "$SECOND_HOME/.claude" "$SECOND_HOME/.cursor"
printf 'second-machine\n' > "$SECOND_HOME/.config/agent-hub/machine"
HOME="$SECOND_HOME" hub init --from "$ORIGIN" --yes
HOME="$SECOND_HOME" hub sync
hub sync
hub status --fleet --json > "$TMP/fleet.json"
python3 - "$TMP/fleet.json" <<'PYFLEET'
import json, sys
rows = json.load(open(sys.argv[1]))["fleet"]
assert {row["machine"] for row in rows} == {"first-machine", "second-machine"}, rows
assert all(row["current"] for row in rows), rows
PYFLEET

echo "== Fleet lag counts content changes, then clears after Sync =="
printf 'Changed on first Machine\n' > "$STORE/AGENTS.md"
hub sync
hub status --fleet --json > "$TMP/fleet.json"
python3 - "$TMP/fleet.json" <<'PYFLEET'
import json, sys
rows = {row["machine"]: row for row in json.load(open(sys.argv[1]))["fleet"]}
assert rows["first-machine"]["current"] is True, rows
assert rows["first-machine"]["local"] is True, rows
assert rows["second-machine"]["behind"] == 1, rows
assert rows["second-machine"]["current"] is False, rows
PYFLEET
HOME="$SECOND_HOME" hub sync
hub sync
hub status --fleet --json > "$TMP/fleet.json"
python3 - "$TMP/fleet.json" <<'PYFLEET'
import json, sys
assert all(row["current"] for row in json.load(open(sys.argv[1]))["fleet"])
PYFLEET
grep -Fq 'Changed on first Machine' "$SECOND_HOME/.claude/CLAUDE.md"

echo "SMOKE TEST PASSED"
