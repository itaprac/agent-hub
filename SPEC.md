# agent-hub v2 — specification

One Git repository, the Store at `~/.agents`, holds your Skills and instructions. `agent-hub` links them into every Agent on this Machine, rewrites the Managed block in each Agent's instruction file, and syncs the Store with any Git remote. Every Machine sees the Fleet through Machine records committed in the Store. No daemon, no token, no Machine-to-Machine call.

This spec replaces the v1 `SPEC.md`, `SPEC-PEERS.md`, and `SPEC-WEB.md`. Vocabulary: `CONTEXT.md`. Decision: `docs/adr/0003`.

## Constraints

| Constraint | Rule |
|---|---|
| Runtime | Python 3.11+, standard library only. Frontend: plain HTML, CSS, JS, no build step, no CDN |
| Install | PyPI package `agent-hub`; `uv tool install agent-hub` or `pipx install agent-hub`. Entry points `agent-hub` and `agent-hub-web` |
| Node | Optional. Needed only for `install` and `update`, which shell out to `npx -y skills` |
| Store | `~/.agents`, or `--store PATH` with `~/.agents` as a symlink to it. Env `AGENT_HUB_STORE` overrides. Tests replace `HOME` |
| Home dirs | Expand `~` and `$VAR`. Honour `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_CONFIG_HOME` where the Agent table uses them |
| Safety | Never write outside the Store, the Agent directories in the table, recorded project directories, and `~/.config/agent-hub`. Never modify text outside a Managed block |
| macOS | No GNU coreutils assumptions. Windows: copy mode instead of symlinks |

## Store layout

```text
~/.agents/
  skills/<name>/SKILL.md ...    # global Skills, hand-written or installed by skills.sh
  .skill-lock.json              # skills.sh provenance, committed
  AGENTS.md                     # global instruction source
  agents/<agent-id>.md          # per-Agent Overlay, optional
  projects/<slug>/skills/<name>/ # private Project skills, optional
  machines/<machine-id>.json    # Machine record, written by Sync
  hub.toml                      # optional filters and custom Agents
  .gitignore                    # created by init
```

Skill directory rule (unchanged from v1): a directory directly under `skills/` whose name does not start with a dot and that contains at least one non-hidden file. Listed in case-insensitive name order.

## Agent table

`agenthub/agents.json`, vendored from the skills.sh agent list (MIT, attribution in `THIRD-PARTY.md`) and refreshed by `tools/refresh_agents.py`. Extended with instruction-file paths that skills.sh does not track.

```json
{
  "claude-code": {
    "name": "Claude Code",
    "universal": false,
    "skills_global": "${CLAUDE_CONFIG_DIR:-~/.claude}/skills",
    "skills_project": ".claude/skills",
    "instructions_global": "${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md",
    "detect": ["${CLAUDE_CONFIG_DIR:-~/.claude}"]
  },
  "codex": {
    "name": "Codex",
    "universal": true,
    "skills_global": "~/.agents/skills",
    "skills_project": ".agents/skills",
    "instructions_global": "${CODEX_HOME:-~/.codex}/AGENTS.md",
    "detect": ["${CODEX_HOME:-~/.codex}"]
  }
}
```

- An Agent is **detected** when any `detect` path exists. Apply targets detected Agents unless `hub.toml [agents] enabled` lists them explicitly.
- A **Universal agent** reads `.agents/skills` natively and gets no skill link. It still gets a Managed block.
- `instructions_global = null` means the Agent has no known global instruction file; skip instructions for it.

## `hub.toml` (optional)

```toml
[agents]
enabled = ["claude-code", "codex"]     # default: detected Agents
mode = "symlink"                       # or "copy"

[agents.my-agent]                      # custom or overridden Agent
name = "My Agent"
universal = false
skills_global = "~/.my/skills"
instructions_global = "~/.my/AGENTS.md"

[skills.mini-tailscale-host]           # optional per-Skill filter
machines = ["mini"]
agents = ["claude-code"]
```

An omitted filter key allows every Machine or Agent. Config errors name the file and key.

## Machine

- Machine ID: `~/.config/agent-hub/machine` if present, else the short hostname (without `.local` or `.lan`), lowercased, non `[a-z0-9-]` replaced by `-`.
- Machine record `machines/<id>.json`:

```json
{
  "machine": "mini",
  "hostname": "mini",
  "os": "darwin",
  "app": "2.0.0",
  "agents": ["claude-code", "codex"],
  "head": "<full sha after pull, before the record commit>",
  "status": {"exit_code": 0, "problems": 0},
  "synced_at": "2026-09-04T16:00:00+02:00"
}
```

- A Machine is **current** when the last commit touching anything outside `machines/` is an ancestor of, or equal to, the record's `head`. Otherwise it is **behind** by that many content commits.
- Sync commits the record only when a field other than `synced_at` changed, or the committed `synced_at` is older than 24 hours. This keeps a 10-minute Timer from filling history.

## Commands

Global flags: `--store PATH`, `--dry-run`, `--quiet`, `--json`. Output is one action or problem per line with prefixes `[ok]`, `[link]`, `[copy]`, `[prune]`, `[render]`, `[skip]`, `[warn]`, `[MISSING]`, `[DRIFT]`, `[STALE]`, `[CONFLICT]`, `[ERROR]`. `main()` returns the exit code.

| Command | Effect |
|---|---|
| `init [--from URL] [--remote URL] [--yes]` | Make the Store. Detect Agents. Adopt existing Skills. Offer to move instruction text into `AGENTS.md`. Set the remote. First commit |
| `apply [--copy]` | Link Skills, prune stale links, render Managed blocks, link recorded Project skills |
| `status [--fleet]` | Report drift and Git state. `--fleet` adds one line per Machine record |
| `sync [--prefer local\|remote]` | Commit, pull with rebase, apply, write the Machine record, push |
| `install SOURCE [--skill NAME]` | `npx -y skills add SOURCE -g -y`, then apply and commit |
| `update [NAME...]` | `npx -y skills update -g`, then apply and commit |
| `add-skill NAME [--project PATH]` | Create `skills/NAME/SKILL.md` from the template with `name` and `description` frontmatter |
| `adopt PATH [--project] [--name NAME]` | Move a real skill directory into the Store and leave a link behind. `--project` files it under the project that contains PATH |
| `project link [PATH]` | Record the project at PATH (default cwd) by its origin URL slug and link its private Skills |
| `timer on\|off\|status` | Install or remove the user scheduler job that runs `sync --quiet` every 10 minutes |
| `ui [--port 7337] [--host 127.0.0.1] [--service on\|off]` | Start the Console, or install it as a user service |
| `migrate PATH` | Convert a v1 Content repo in place into the Store layout and print what needs manual action |

### `init`

1. If `~/.agents` is missing, create it. If `--store PATH` is given, create PATH and make `~/.agents` a symlink to it. If `~/.agents` exists and is not a Git repository, keep its contents; they become the first commit.
2. With `--from URL`: clone into the Store. If the Store already holds Skills, move each into the clone; a name clash stops with a clear message.
3. Detect Agents and print them.
4. For each detected non-Universal Agent, list real skill directories (not links) in its global skills directory and offer to adopt them. `--yes` adopts all.
5. For each detected Agent with an instruction file that has no Managed block, offer to move its text into `AGENTS.md` and leave a Managed block in place. Text is moved once, never merged. `--yes` skips this step.
6. Write `.gitignore` (`.DS_Store`, `*.local.*`) and commit `init: <machine-id>`.
7. With `--remote URL`: add `origin` and push. Without it, print how to add one later and how to `init --from` on the next Machine.

### `apply`

For every targeted Agent and every Skill that passes the `hub.toml` filters:

- **Symlink mode:** for a non-Universal Agent, create `<skills_global>/<name>` as a relative link to the Store skill, the way skills.sh does. Keep a correct link, whether relative or absolute. Replace a link into the Store that points elsewhere. Never overwrite a real file or directory: report `[DRIFT]`, suggest `adopt`, continue, and exit 1 at the end.
- **Copy mode:** copy with `shutil.copytree(..., dirs_exist_ok=True)` when file hashes differ. Remove extra files only inside that Skill's target directory.
- **Prune:** remove links in the Agent directory that resolve into the Store's `skills/` tree and are no longer selected. Never prune copies, foreign links, or real directories. `status` reports them as `[STALE]`.
- **Instructions:** if `AGENTS.md` exists, render it, then a blank line and `agents/<agent-id>.md` if present, as the Managed block:

  ```text
  <!-- agent-hub:begin -->
  <!-- Managed by agent-hub. Edit ~/.agents/AGENTS.md; local edits inside this block are overwritten. -->
  ...rendered content...
  <!-- agent-hub:end -->
  ```

  Missing file: create it with only the block. No markers: append after a blank line. One balanced pair: replace the block. Any other marker state is `[DRIFT]` and the file is left untouched. Write only when content changes. Create missing parent directories.
- **Project skills:** for each recorded project, link `projects/<slug>/skills/<name>` into the project's `.agents/skills/<name>` and, for non-Universal Agents, into their project skills directory. Add each link path to the project's `.git/info/exclude`. A recorded project whose path is missing is `[skip]`.

### `sync`

1. Dirty Store: `git add -A`, commit `sync: <machine-id>`.
2. With an upstream: `git pull --rebase`. If the remote is unreachable, report `[warn] origin unreachable; push pending` and continue. On a conflict: `git rebase --abort`, report `[CONFLICT]` with the file names, do not apply, exit 1. `--prefer local` or `--prefer remote` retries the pull resolving conflicts toward that side.
3. `apply`.
4. Write the Machine record; commit it when the rule above says so.
5. With an upstream and a reachable remote: `git push`. Without a remote: report `[skip] no remote; pull and push disabled`.

Every operation on the Store runs under one process-wide lock; a concurrent request gets `[ERROR] store is busy` (HTTP 423 in the Console).

### `status --fleet`

One line per Machine record: ID, current or behind N, problems, age of `synced_at`, and `local` for this Machine. Exit 0 when this Machine is clean and current, else 1.

### `migrate PATH`

In place, with `git mv` so history survives: `skills/global/*` to `skills/`, `skills/projects/<p>/*` to `projects/<slug>/skills/` when `projects.toml` maps `<p>` to a path with an origin URL on this Machine, `instructions/global/base.md` to `AGENTS.md`, `instructions/global/<agent>.md` to `agents/<agent-id>.md` (v1 `claude` becomes `claude-code`), `skills.toml` restrictions to `hub.toml [skills.*]`. Print manual steps for `instructions/projects/*` and drop `hub.toml [machines]`, `agents.toml`, `projects.toml`, and `peers.toml` with a report. Then the operator runs `init --store PATH`.

## Console (`agent-hub ui`)

The v1 Web UI minus federation. `agenthub.webapp` serves `web/` and a JSON API; it calls package interfaces, never CLI output. Binds to `127.0.0.1:7337`; network exposure stays manual (`docs/network-exposure.md`). No authentication: mutations require a same-origin browser request.

| Route | Returns |
|---|---|
| `GET /api/state` | Machine ID, Store path, Agents with detection and paths, Skills with files and lockfile provenance, instruction files, `hub.toml` presence |
| `GET /api/status` | The structured status report |
| `GET /api/git` | Branch, HEAD, dirty, ahead, behind; `?fetch=0` skips fetch |
| `GET /api/fleet` | Every Machine record with current or behind and `synced_at` age |
| `POST /api/run {command, dry_run}` | `apply`, `sync`, `install {source, skill?}`, `update {names?}`; structured report |
| `POST /api/add-skill`, `POST /api/adopt` | As the CLI |
| `GET/PUT/DELETE /api/file` | Text files under `skills/**`, `AGENTS.md`, `agents/*.md`, `projects/**`, `hub.toml`; revision check with 428 and 409; `hub.toml` parsed before write, 422 on error; 1 MB limit; traversal rejected |

Frontend sections: top bar with Refresh and colour scheme; Fleet panel with one card per Machine record (no buttons on remote cards); local status bar and checks; Skills tree with editor, New, Adopt, and Install; Instructions editor for `AGENTS.md` and Overlays; Config editor for `hub.toml`. `DESIGN.md` governs appearance.

## Non-goals

No server mode, no shared token, no remote actions, no cross-Machine Usage, no database, no project instructions in the Store, no automatic edits to a project's committed files.

## Tests

- pytest: Agent table loading and expansion, Machine ID, filters, apply on a fake `HOME` with fake Agents, Managed block states, Sync with a bare remote in `mktemp -d` including unreachable remote and conflict paths, Machine record commit rule, fleet current or behind, Console HTTP contract, release-safety scan.
- Node: `tests/web_state.mjs`, `tests/web_theme.mjs`.
- `tests/smoke.sh`: init on a fake `HOME` with two fake Agents, apply, drift, stale, adopt, add-skill, sync against a bare remote, second fake Machine through `init --from`, `status --fleet` shows both.
- CI runs all of the above on every supported Python version. Nothing touches the real `HOME`.
