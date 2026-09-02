# agent-hub — MVP specification

One Content Git repository is the source of truth for AI agent skills and instructions (Claude Code, Codex, and others). The App deploys them to target paths, and Git synchronizes the Machines in the fleet.

## Technical requirements

- Python 3.11+ (`tomllib`), standard library only. The behavior lives in the `agenthub` package; the installed `agent-hub` command is the entry point, and `hub.py` is a thin compatibility shim over the same package.
- Run as the installed `agent-hub <command>`, or as `./hub.py <command>` from a checkout.
- The content repository is resolved from `--repo PATH`, then `AGENT_HUB_REPO`, then `~/.config/agent-hub/root`.
- Expand `~` with `os.path.expanduser`; tests replace `HOME`.
- Support macOS without assuming GNU coreutils.

## Repository layout

App files (public repository):

```text
agent-hub/
  hub.py, web.py, usage.py   # compatibility shims
  agenthub/                  # package: core, cli, config, fileio, files,
                             #   gitio, operations, setup, usage, webapp
  setup.sh                   # installer (see the setup spec in issue #23/#24)
  example-content/           # starter Content repository
  web/                       # Web UI frontend
  tests/                     # pytest suite, node tests, smoke scripts
```

Content files (private repository; a mixed checkout holds both until the split):

```text
  config/hub.toml        # [machines] hostname -> machine ID
  config/agents.toml     # adapters
  config/projects.toml   # [project] machine_id = "path"
  config/skills.toml     # optional [skill] agents/machines restrictions
  skills/global/<skill>/SKILL.md ...
  skills/projects/<project>/<skill>/...
  instructions/global/base.md and <agent>.md
  instructions/projects/<project>/base.md and <agent>.md
```

The configuration files already exist; preserve their format.

## Data model and resolution

- **Machine:** Resolve the ID from `[machines]` in `hub.toml`. Try the exact `platform.node()`, then the hostname without `.local` or `.lan`. An unknown hostname must produce a clear error explaining what to add to `hub.toml`.
- **Agent:** A section in `agents.toml`. Supported keys are `skills_global`, `skills_project`, `instructions_global`, `instructions_project`, and `mode` (`"symlink"` by default or `"copy"`). A missing key means the agent does not support that category.
- **Global skill:** A skill directory under `skills/global/` (see CONTEXT.md: non-hidden, with at least one non-hidden file, listed in case-insensitive name order). Deploy it to each agent with `skills_global`, unless `skills.toml` restricts its `agents` and/or `machines`. The same optional restrictions apply to project skills; an omitted key allows every configured agent or machine.
- **Project skill:** A skill directory under `skills/projects/<project>/` (same rule as a global skill). Deploy it only when the project has a path for the current machine and that path exists. A missing path is a status warning, not an error.
- **Instructions:** Render `base.md`, then, if present, a blank line and `<agent>.md`. Insert the result as a managed block:

  ```text
  <!-- agent-hub:begin -->
  <!-- Managed by agent-hub. Edit in the content repo; local edits are overwritten. -->
  ...rendered content...
  <!-- agent-hub:end -->
  ```

  If the target does not exist, create it with only the block and a trailing newline. If it has no markers, append the block after a blank line. If exactly one stable begin and end marker pair exists, replace the whole managed block. Any other marker state — a retired or malformed begin marker, an unbalanced pair, or duplicate blocks — is reported loudly as drift and the file is left untouched. Never modify content outside the markers. If neither `base.md` nor the agent overlay exists, do nothing and do not create an empty block.

## Commands

Global flags: `--repo PATH` and `--dry-run`. For apply and sync, dry-run prints actions without changing anything. Output is human-readable, one action or problem per line, with prefixes such as `[ok]`, `[link]`, `[copy]`, `[prune]`, `[render]`, `[skip]`, `[MISSING]`, `[DRIFT]`, and `[ERROR]`.

### `hub apply`

Bring targets to the state declared in the repository:

- Symlink mode: create a symlink from the target to the repository directory. Keep a correct link; replace a link to another location. Never overwrite a regular file or directory: report `[DRIFT]`, suggest `hub adopt <path>`, continue, and exit 1 at the end.
- Copy mode: when file hashes differ, copy with `shutil.copytree(..., dirs_exist_ok=True)`. Remove extra files only inside that skill's target directory.
- In symlink mode, remove stale links whose names are no longer selected after agent/machine filtering only when their resolved destinations remain inside this repository's `skills/` tree; report the same links as `[STALE]` in `status`, and never prune copy-mode targets or foreign links.
- Render instruction managed blocks and write only when content changes.
- Create missing parent directories such as `~/.claude/skills`.

### `hub status`

Make no changes. Report per agent and project:

- Missing targets (`[MISSING]`), incorrect symlinks, or regular files/directories where a symlink is expected (`[DRIFT]`).
- Copy-mode content differences (`[DRIFT]`).
- Missing or outdated managed blocks (`[STALE]`).
- Projects without a path on this machine or whose path does not exist (`[skip]`).
- Git uncommitted changes and ahead/behind counts using `git status --porcelain` and `git rev-list --left-right --count @{u}...HEAD`; report when no upstream exists.

Exit 0 when clean, otherwise exit 1.

### `hub sync`

1. If the working tree is dirty, run `git add -A` and commit as `hub sync: <machine-id>`.
2. With a remote/upstream, run `git pull --rebase`. On a rebase error, stop with a clear message; do not apply or push.
3. Run `apply`.
4. With a remote, run `git push`.

Without a remote, run only steps 1 and 3 and report that choice.

### `hub add-skill NAME [--project PROJECT]`

Create `skills/global/NAME/SKILL.md`, or `skills/projects/PROJECT/NAME/SKILL.md`, with minimal `name` and `description` frontmatter. Fail if the skill exists or the project is unknown.

### `hub adopt PATH [--project PROJECT] [--name NAME]`

- `PATH` must be an existing directory, not a symlink.
- `NAME` defaults to the path basename.
- Move the directory to `skills/global/NAME` or the project equivalent, then create a symlink at the original path. Fail if the repository destination exists.
- Remind the user to run `hub apply` for other agents.

## Code quality

- Prefer functions over unnecessary classes; keep code flat and readable; use argparse subparsers; make `main()` return the exit code.
- Use `pathlib.Path` for all filesystem operations.
- Configuration errors must name the file and key to fix.

## Tests

The primary suite is pytest (`tests/test_*.py`): unit tests for the package, HTTP contract tests for the Web adapter, and the release-safety scan. Node tests (`tests/web_state.mjs`, `tests/web_theme.mjs`) cover frontend state and colour schemes. CI (`.github/workflows/ci.yml`) runs syntax checks, pytest, the node tests, and the three smoke scripts (`smoke.sh`, `web_smoke.sh`, `smoke-peers.sh`) on every supported Python version.

### End-to-end smoke: `tests/smoke.sh`

Run from the repository root; exit 0 means success. The script builds a complete fixture in `mktemp -d`: fake `HOME`, repository copy, dynamically detected hostname in `hub.toml`, test project, global and project skills, and instructions.

It must verify at least:

1. `apply` creates symlinks and managed blocks, including appending to a `CLAUDE.md` while preserving user content.
2. `status` exits 0 after apply; replacing a symlink with a regular directory makes it exit 1 with `[DRIFT]`.
3. Editing `base.md` produces `[STALE]`; `apply` fixes it.
4. `adopt` moves a directory and leaves a symlink.
5. `add-skill` creates the template.
6. `sync` in a Git repository without a remote commits dirty changes and applies them.

The test must never touch the real home directory. Every `hub.py` call uses `HOME=$TMP`, and fixture paths use `~/...`.
