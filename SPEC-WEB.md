# agent-hub — Web UI MVP specification

A small local frontend for the `agenthub` package. It shows status, runs apply and sync, and lets users browse and edit skills, instructions, and configuration without using a terminal. It runs on any Machine in the fleet and may be shared over a trusted private network (see `docs/network-exposure.md`).

## Hard constraints

- **No dependencies:** the backend uses only the Python 3.11+ standard library. The frontend is plain HTML, CSS, and JavaScript in `web/`, with no build step or external resources. It must work offline with system fonts.
- The Web adapter calls structured `agenthub` interfaces for every operation. It never loads application code from the Content repo and never parses CLI output. HTTP dispatch uses one method, route-pattern, and handler table.
- File operations are limited to the repository. Resolve every request path and verify it is below the repository root to prevent traversal. Allow only text files with `.md`, `.toml`, `.txt`, `.sh`, `.py`, `.json`, `.yaml`, or `.yml` extensions, up to 1 MB.
- Browser access is authorized by the private-network boundary (Tailscale ACLs or an equivalent reverse-proxy policy) and binds to `127.0.0.1` by default. Mutations require a same-origin browser request; direct peer calls use the server-only token described below.

## Backend: package Web adapter

`agenthub.webapp` owns the HTTP adapter. `agent-hub-web [--port 7337] [--host 127.0.0.1] [--repo PATH]` is the installed entry point; `./web.py` remains a compatibility shim over the same package implementation. The repository is resolved from `--repo`, then `AGENT_HUB_REPO`, then `~/.config/agent-hub/root`.

JSON API:

- `GET /api/state` — machine ID, hostname, repository path, agents with their `agents.toml` keys, projects with paths and availability on this machine, the global and per-project skill tree with file lists, the instruction tree with existing base/overlay files, and the config file list.
- `GET /api/status` — report status from the shared package; return `{exit_code, problems, machine_id, hostname, repo, lines: [{level, text}], checks}`, where `level` is the prefix without brackets and each check carries its kind, agent, project, name, and target.
- `GET /api/git` — local branch and HEAD plus dirty/ahead/behind counts; `?fetch=0` skips `git fetch`.
- `GET /api/peers` — aggregate Git state and status from every machine in `config/peers.toml`; query remote backends server-side.
- `POST /api/run {command: "apply"|"sync", dry_run: bool}` — run the command through the shared package and return the structured report.
- `POST /api/peers/{machine}/run` — run apply or sync locally or through the configured peer backend.
- `POST /api/add-skill {name, project?}` and `POST /api/adopt {path, project?, name?}` — call the shared package and return its structured report.
- `GET /api/file?path=<repo-relative>` — return `{path, content, revision}`, where `revision` is the SHA-256 hash of the file bytes.
- `PUT /api/file {path, content, revision}` — atomically write a file and create missing parent directories. Send the revision returned by `GET`, or `null` when creating a new file.
- `DELETE /api/file {path, revision}` — delete a repository file, not a directory; the UI requires confirmation.
- File mutations without a revision return `428`. A stale revision returns `409` without changing the file; the editor lets the user keep their draft, reload, or explicitly overwrite the latest version.
- Before writing direct `config/*.toml` files, parse the draft as TOML. Invalid content returns `422` with the parser's line and column and leaves the file unchanged.
- Return errors as `{error}` with an appropriate HTTP status. Set `Cache-Control: no-store` on every response.
- Load the peer token from `AGENT_HUB_PEER_TOKEN` or `~/.config/agent-hub/peer-token` (mode `0600`), never from Git. Only server-to-server `POST /api/run` accepts `X-Hub-Token`; browser mutations require a same-origin request and never receive the token.
- File reads and writes are limited to `skills/**`, `instructions/**`, and direct `config/*.toml` files. In particular, the API cannot edit executable application files such as `hub.py` or `web.py`.
- Serve `/` from `web/index.html` and other static paths from `web/`, with traversal protection.

## Frontend: `web/`

Use vanilla JavaScript, optionally as a single `app.js` with `style.css`.

1. **Top bar:** name, machine ID and hostname, a global Refresh button (reloads state, status, and machines; shortcut `R`), and a colour-scheme menu offering Dark, Black, Light, and System, in that order; Dark is the default, System follows the OS appearance and maps OS dark to Dark rather than Black, and the choice persists in `localStorage`. All Apply/Sync actions live on the machine cards (SPEC-PEERS.md section 5), with a shared dry-run checkbox in the Machines panel header.
2. **Dashboard / Status:** a single compact status bar (verdict on the left, non-zero problem counters on the right, full breakdown in a tooltip), then the structured status checks grouped by agent with colored badges: green for ok, orange/red for MISSING, DRIFT, and STALE, and gray for skip. An All/Problems filter appears only when there are problems. Apply/sync output goes to a slide-up log panel with raw, colored monospace lines.
3. **Skills:** sidebar tree grouped into Global and projects. Selecting a skill opens its file list and a monospace editor. Cmd+S and Save write changes; show unsaved state. Provide New skill and Adopt modals. Confirm before deleting a file.
4. **Instructions:** the same pattern for global and project `base.md` and `<agent>.md` overlays. Provide a button to create a missing file.
5. **Config:** raw-text editing for the TOML files in `config/`.
6. Refresh status automatically after every save or action.

The UI should be clean, information-dense, and tool-like. Fully support light and dark `prefers-color-scheme`. Do not use CSS frameworks.

## Test: `tests/web_smoke.sh`

Create a fixture repository and fake `HOME` in `mktemp -d`, start `web.py` on a free port with `HOME=$TMP`, wait until ready, then verify with curl:

- `GET /` returns HTML.
- `GET /api/state` returns valid JSON with the machine ID and skills.
- `GET /api/status` returns `exit_code` and lines.
- `POST /api/run` can dry-run apply.
- `PUT` followed by `GET /api/file` round-trips; stale concurrent writes return `409`, executable permissions survive atomic replacement, and traversal returns 4xx.
- `POST /api/add-skill` creates a skill in the fixture.

Stop the server with a trap. Exit 0 means success. Never touch the real `HOME`.

## README

Document how to start the Web UI on localhost and point optional network exposure at `docs/network-exposure.md`. State that it has no general-purpose authentication and must be available only on localhost or a trusted private network.
