#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

REPO="$TMP/repo"
FAKE_HOME="$TMP/home"
PROJECT="$TMP/project"
mkdir -p "$FAKE_HOME" "$PROJECT"
cp -R "$ROOT" "$REPO"
rm -rf -- "$REPO/.git" "$REPO/__pycache__" "$REPO/.venv" "$REPO/.pytest_cache"

# macOS exposes /var as a symlink to /private/var. Canonical paths keep
# readlink assertions independent of which spelling mktemp returned.
REPO="$(CDPATH= cd -- "$REPO" && pwd -P)"
FAKE_HOME="$(CDPATH= cd -- "$FAKE_HOME" && pwd -P)"
PROJECT="$(CDPATH= cd -- "$PROJECT" && pwd -P)"

export HOME="$FAKE_HOME"
HOSTNAME="$(python3 -c 'import platform; print(platform.node())')"

python3 - "$REPO" "$PROJECT" "$HOSTNAME" <<'PY'
from pathlib import Path
import sys

repo = Path(sys.argv[1])
project = Path(sys.argv[2])
hostname = sys.argv[3]

# The App repo carries no config/; the fixture creates the Content shape itself.
(repo / "config").mkdir(parents=True, exist_ok=True)
(repo / "config" / "hub.toml").write_text(
    f'[machines]\n"{hostname}" = "testmachine"\nunused-host = "other-machine"\n',
    encoding="utf-8",
)
(repo / "config" / "agents.toml").write_text(
    """[claude]
skills_global = "~/.claude/skills/{name}"
skills_project = "{project_root}/.claude/skills/{name}"
instructions_global = "~/.claude/CLAUDE.md"
instructions_project = "{project_root}/CLAUDE.md"
mode = "symlink"

[copybot]
skills_global = "~/copybot/skills/{name}"
mode = "copy"
""",
    encoding="utf-8",
)
(repo / "config" / "projects.toml").write_text(
    f'[demo]\ntestmachine = "{project}"\n\n[missing-project]\nother-machine = "~/missing"\n',
    encoding="utf-8",
)
(repo / "config" / "skills.toml").write_text(
    """[global-one]
agents = ["claude", "copybot"]

[global-current-machine]
agents = ["claude"]
machines = ["testmachine"]

[global-other-machine]
machines = ["other-machine"]

[project-current-machine]
machines = ["testmachine"]

[project-other-machine]
machines = ["other-machine"]
""",
    encoding="utf-8",
)

files = {
    repo / "skills" / "global" / "global-one" / "SKILL.md": "# Global fixture\n",
    repo / "skills" / "global" / "global-current-machine" / "SKILL.md": "# Current machine\n",
    repo / "skills" / "global" / "global-other-machine" / "SKILL.md": "# Other machine\n",
    repo / "skills" / "projects" / "demo" / "project-one" / "SKILL.md": "# Project fixture\n",
    repo / "skills" / "projects" / "demo" / "project-current-machine" / "SKILL.md": "# Current machine\n",
    repo / "skills" / "projects" / "demo" / "project-other-machine" / "SKILL.md": "# Other machine\n",
    repo / "instructions" / "global" / "base.md": "Global base v1\n",
    repo / "instructions" / "global" / "claude.md": "Claude global overlay\n",
    repo / "instructions" / "projects" / "demo" / "base.md": "Project base v1\n",
    repo / "instructions" / "projects" / "demo" / "claude.md": "Claude project overlay\n",
}
for path, content in files.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

(Path.home() / ".claude").mkdir(parents=True, exist_ok=True)
(Path.home() / ".claude" / "CLAUDE.md").write_bytes(
    b"user-owned global prefix\r\n\r\n"
    b"<!-- agent-hub:begin -->\r\n"
    b"old managed content\r\n"
    b"<!-- agent-hub:end -->\r\n\r\n"
    b"user-owned global suffix\r\n"
)
(project / "CLAUDE.md").write_text("user-owned project text\n", encoding="utf-8")
PY

git -C "$REPO" init -q
git -C "$REPO" config user.name "agent-hub smoke"
git -C "$REPO" config user.email "smoke@example.invalid"
git -C "$REPO" add -A
git -C "$REPO" commit -qm "fixture"

hub() {
    python3 "$REPO/hub.py" --repo "$REPO" "$@"
}

HUB_OUTPUT=""
run_hub() {
    if HUB_OUTPUT="$(hub "$@" 2>&1)"; then
        return 0
    fi
    local status=$?
    printf '%s\n' "$HUB_OUTPUT" >&2
    echo "COMMAND FAILED: hub $* (exit $status)" >&2
    return "$status"
}

assert_file_contains() {
    local file="$1"
    local text="$2"
    if ! grep -Fq -- "$text" "$file"; then
        echo "ASSERTION FAILED: '$file' does not contain '$text'" >&2
        exit 1
    fi
}

assert_symlink_to() {
    local link="$1"
    local destination="$2"
    if [[ ! -L "$link" || "$(readlink "$link")" != "$destination" ]]; then
        echo "ASSERTION FAILED: '$link' is not a symlink to '$destination'" >&2
        exit 1
    fi
}

echo "== 1. apply creates links, copies, and managed blocks =="
run_hub --dry-run apply
test ! -e "$FAKE_HOME/.claude/skills/global-one"
test ! -e "$PROJECT/.claude/skills/project-one"
run_hub apply
assert_symlink_to "$FAKE_HOME/.claude/skills/global-one" "$REPO/skills/global/global-one"
assert_symlink_to "$PROJECT/.claude/skills/project-one" "$REPO/skills/projects/demo/project-one"
assert_symlink_to "$FAKE_HOME/.claude/skills/global-current-machine" "$REPO/skills/global/global-current-machine"
assert_symlink_to "$PROJECT/.claude/skills/project-current-machine" "$REPO/skills/projects/demo/project-current-machine"
test ! -e "$FAKE_HOME/.claude/skills/global-other-machine"
test ! -e "$PROJECT/.claude/skills/project-other-machine"
test ! -e "$FAKE_HOME/copybot/skills/global-current-machine"
test ! -e "$FAKE_HOME/copybot/skills/global-other-machine"
test -f "$FAKE_HOME/copybot/skills/global-one/SKILL.md"
test ! -L "$FAKE_HOME/copybot/skills/global-one"
assert_file_contains "$FAKE_HOME/.claude/CLAUDE.md" "Global base v1"
assert_file_contains "$FAKE_HOME/.claude/CLAUDE.md" "Claude global overlay"
assert_file_contains "$PROJECT/CLAUDE.md" "user-owned project text"
assert_file_contains "$PROJECT/CLAUDE.md" "Project base v1"
assert_file_contains "$PROJECT/CLAUDE.md" "<!-- agent-hub:begin"
python3 - "$FAKE_HOME/.claude/CLAUDE.md" "$PROJECT/CLAUDE.md" <<'PY'
from pathlib import Path
import sys

expected_contents = (
    (
        "user-owned global prefix\r\n\r\n"
        "<!-- agent-hub:begin -->\n"
        "<!-- Managed by agent-hub. Edit in the content repo; local edits are overwritten. -->\n"
        "Global base v1\n\n"
        "Claude global overlay\n"
        "<!-- agent-hub:end -->\r\n\r\n"
        "user-owned global suffix\r\n"
    ),
    (
        "user-owned project text\n\n"
        "<!-- agent-hub:begin -->\n"
        "<!-- Managed by agent-hub. Edit in the content repo; local edits are overwritten. -->\n"
        "Project base v1\n\n"
        "Claude project overlay\n"
        "<!-- agent-hub:end -->\n"
    ),
)
for value, expected in zip(sys.argv[1:], expected_contents, strict=True):
    actual = Path(value).read_bytes()
    assert actual == expected.encode(), actual.decode()
PY
echo "PASS"

echo "== 2a. managed blocks are stable =="
cp "$FAKE_HOME/.claude/CLAUDE.md" "$TMP/global-instructions.after-first-apply"
cp "$PROJECT/CLAUDE.md" "$TMP/project-instructions.after-first-apply"
run_hub status
run_hub apply
cmp "$TMP/global-instructions.after-first-apply" "$FAKE_HOME/.claude/CLAUDE.md"
cmp "$TMP/project-instructions.after-first-apply" "$PROJECT/CLAUDE.md"
echo "PASS"

echo "== 2b. malformed marker pairs fail without changing the file =="
cp "$PROJECT/CLAUDE.md" "$TMP/project-instructions.valid"
for marker_case in missing-end orphan-end reversed duplicate-begin duplicate-end; do
    python3 - "$PROJECT/CLAUDE.md" "$marker_case" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
marker_case = sys.argv[2]
cases = {
    "missing-end": "<!-- agent-hub:begin -->\nmanaged content\n",
    "orphan-end": "managed content\n<!-- agent-hub:end -->\n",
    "reversed": "<!-- agent-hub:end -->\nmanaged content\n<!-- agent-hub:begin -->\n",
    "duplicate-begin": (
        "<!-- agent-hub:begin -->\n"
        "managed content\n"
        "<!-- agent-hub:begin -->\n"
        "<!-- agent-hub:end -->\n"
    ),
    "duplicate-end": (
        "<!-- agent-hub:begin -->\n"
        "managed content\n"
        "<!-- agent-hub:end -->\n"
        "<!-- agent-hub:end -->\n"
    ),
}
path.write_text("operator prefix\n" + cases[marker_case] + "operator suffix\n", encoding="utf-8")
PY
    cp "$PROJECT/CLAUDE.md" "$TMP/project-instructions.malformed"
    set +e
    MALFORMED_STATUS_OUTPUT="$(hub status 2>&1)"
    MALFORMED_STATUS_RC=$?
    MALFORMED_APPLY_OUTPUT="$(hub apply 2>&1)"
    MALFORMED_APPLY_RC=$?
    set -e
    test "$MALFORMED_STATUS_RC" -eq 1
    test "$MALFORMED_APPLY_RC" -eq 1
    grep -Fq "missing or malformed managed markers" <<<"$MALFORMED_STATUS_OUTPUT"
    grep -Fq "malformed or duplicate managed markers" <<<"$MALFORMED_APPLY_OUTPUT"
    cmp "$TMP/project-instructions.malformed" "$PROJECT/CLAUDE.md"
done
cp "$TMP/project-instructions.valid" "$PROJECT/CLAUDE.md"
run_hub status
echo "PASS"

echo "== 2c. a regular directory replacing a link is drift =="
rm -- "$FAKE_HOME/.claude/skills/global-one"
mkdir -p "$FAKE_HOME/.claude/skills/global-one"
set +e
DRIFT_OUTPUT="$(hub status 2>&1)"
DRIFT_RC=$?
set -e
test "$DRIFT_RC" -eq 1
grep -Fq "[DRIFT]" <<<"$DRIFT_OUTPUT"
rm -rf -- "$FAKE_HOME/.claude/skills/global-one"
run_hub apply
echo "PASS"

echo "== 2d. apply prunes only stale repository skill symlinks =="
mkdir -p "$TMP/external-skill"
ln -s "$TMP/external-skill" "$FAKE_HOME/.claude/skills/foreign-skill"
python3 - "$REPO/config/skills.toml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
content = path.read_text(encoding="utf-8")
content = content.replace(
    '[global-current-machine]\nagents = ["claude"]\nmachines = ["testmachine"]',
    '[global-current-machine]\nagents = ["claude"]\nmachines = ["other-machine"]',
)
path.write_text(content, encoding="utf-8")
PY
set +e
PRUNE_STATUS_OUTPUT="$(hub status 2>&1)"
PRUNE_STATUS_RC=$?
set -e
test "$PRUNE_STATUS_RC" -eq 1
grep -Fq "[STALE] orphaned skill symlink" <<<"$PRUNE_STATUS_OUTPUT"
run_hub --dry-run apply
grep -Fq "[prune] would remove $FAKE_HOME/.claude/skills/global-current-machine" <<<"$HUB_OUTPUT"
test -L "$FAKE_HOME/.claude/skills/global-current-machine"
run_hub apply
grep -Fq "[prune] remove $FAKE_HOME/.claude/skills/global-current-machine" <<<"$HUB_OUTPUT"
test ! -L "$FAKE_HOME/.claude/skills/global-current-machine"
test ! -e "$FAKE_HOME/.claude/skills/global-current-machine"
assert_symlink_to "$FAKE_HOME/.claude/skills/foreign-skill" "$TMP/external-skill"
echo "PASS"

echo "== 3. changed instructions are stale and apply repairs them =="
printf '\nGlobal base v2\n' >> "$REPO/instructions/global/base.md"
printf 'remove me\n' > "$FAKE_HOME/copybot/skills/global-one/extra.txt"
set +e
STALE_OUTPUT="$(hub status 2>&1)"
STALE_RC=$?
set -e
test "$STALE_RC" -eq 1
grep -Fq "[STALE]" <<<"$STALE_OUTPUT"
run_hub apply
assert_file_contains "$FAKE_HOME/.claude/CLAUDE.md" "Global base v2"
assert_file_contains "$PROJECT/CLAUDE.md" "user-owned project text"
test ! -e "$FAKE_HOME/copybot/skills/global-one/extra.txt"
echo "PASS"

echo "== 4. adopt moves a directory and leaves a repository link =="
mkdir -p "$FAKE_HOME/.claude/skills/adopted"
printf '# Adopted fixture\n' > "$FAKE_HOME/.claude/skills/adopted/SKILL.md"
run_hub adopt "$FAKE_HOME/.claude/skills/adopted"
assert_symlink_to "$FAKE_HOME/.claude/skills/adopted" "$REPO/skills/global/adopted"
test -f "$REPO/skills/global/adopted/SKILL.md"
echo "PASS"

echo "== 5. add-skill creates a minimal template =="
run_hub add-skill new-skill
assert_file_contains "$REPO/skills/global/new-skill/SKILL.md" "name: new-skill"
assert_file_contains "$REPO/skills/global/new-skill/SKILL.md" "description:"

# The skill-name rules are covered directly at the package seam by
# tests/test_config_validation.py; this suite keeps the CLI behavior only.
set +e
INVALID_NAME_OUTPUT="$(hub add-skill '.hidden' 2>&1)"
INVALID_NAME_RC=$?
set -e
test "$INVALID_NAME_RC" -eq 1
grep -Fq "invalid skill name" <<<"$INVALID_NAME_OUTPUT"
test ! -e "$REPO/skills/global/.hidden"
echo "PASS"

echo "== 6. sync without a remote commits and applies =="
run_hub sync
grep -Fq "no remote configured" <<<"$HUB_OUTPUT"
test -z "$(git -C "$REPO" status --porcelain)"
test "$(git -C "$REPO" log -1 --pretty=%s)" = "hub sync: testmachine"
assert_symlink_to "$FAKE_HOME/.claude/skills/new-skill" "$REPO/skills/global/new-skill"
test -f "$FAKE_HOME/copybot/skills/adopted/SKILL.md"
run_hub status
echo "PASS"

echo "== 7. sync reloads pulled skill restrictions before apply =="
SYNC_REMOTE="$TMP/sync-remote.git"
SYNC_A="$TMP/sync-a"
SYNC_B="$TMP/sync-b"
SYNC_HOME="$TMP/sync-home"
mkdir -p "$SYNC_HOME"
git clone -q --bare "$REPO" "$SYNC_REMOTE"
git clone -q "$SYNC_REMOTE" "$SYNC_A"
git -C "$SYNC_A" config user.name "agent-hub smoke"
git -C "$SYNC_A" config user.email "smoke@example.invalid"
git clone -q "$SYNC_REMOTE" "$SYNC_B"
mkdir -p "$SYNC_A/skills/global/pulled-other-machine"
printf '# Pulled fixture\n' > "$SYNC_A/skills/global/pulled-other-machine/SKILL.md"
cat >> "$SYNC_A/config/skills.toml" <<'EOF'

[pulled-other-machine]
machines = ["other-machine"]
EOF
git -C "$SYNC_A" add -A
git -C "$SYNC_A" commit -qm "add machine-restricted skill"
git -C "$SYNC_A" push -q
SYNC_OUTPUT="$(HOME="$SYNC_HOME" python3 "$SYNC_B/hub.py" --repo "$SYNC_B" sync 2>&1)"
test ! -e "$SYNC_HOME/.claude/skills/pulled-other-machine"
grep -Fq "git pull --rebase" <<<"$SYNC_OUTPUT"
echo "PASS"

echo "== 8. unknown skill machine IDs are configuration errors =="
cat >> "$REPO/config/skills.toml" <<'EOF'

[invalid-machine]
machines = ["not-configured"]
EOF
set +e
INVALID_MACHINE_OUTPUT="$(hub status 2>&1)"
INVALID_MACHINE_RC=$?
set -e
test "$INVALID_MACHINE_RC" -eq 2
grep -Fq "key 'invalid-machine.machines'" <<<"$INVALID_MACHINE_OUTPUT"
grep -Fq "unknown machine id 'not-configured'" <<<"$INVALID_MACHINE_OUTPUT"
echo "PASS"

echo "SMOKE TEST PASSED"
