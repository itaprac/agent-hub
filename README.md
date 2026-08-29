# agent-hub

`agent-hub` keeps AI agent skills and instructions in one Git repository (the Content repo) and deploys them to the paths used by Claude Code, Codex, and other adapters. The App (this repository) is the engine: a CLI, a Web UI, and usage analytics. Python 3.11+, standard library only. MIT licensed, see `LICENSE`.

## Concepts

| Term | Meaning |
|---|---|
| App repo | This public repository: the engine, tests, and docs. |
| Content repo | Your private repository: skills, instructions, and Fleet config. |
| Machine | A trusted host with a stable machine ID, mapped in `config/hub.toml`. |
| Peer | A Machine reachable from another Machine's Web UI for remote status and actions. |
| Fleet config | The `config/` files that declare machines, agents, projects, skills, and peers. |
| Managed block | The region between `<!-- agent-hub:begin -->` and `<!-- agent-hub:end -->` that apply rewrites. Text outside it is never touched. |

## Install in five minutes

```bash
git clone https://github.com/itaprac/agent-hub.git && cd agent-hub
./setup.sh
```

Setup can use a local Content repo, clone one, or create one from `example-content/`. It registers this Machine, installs the App into `.venv`, writes the Content pointer (`~/.config/agent-hub/root`), runs status, and verifies the local Web UI. It never runs apply; review the dry-run command it prints. Every prompt has an unattended flag, for example:

```bash
./setup.sh --new-content ../agent-hub-content --machine workstation --non-interactive
```

On macOS, setup installs the `com.agenthub.web` user service on `127.0.0.1:7337`. On Linux, it prints the foreground Web command. `./setup.sh --update` fast-forwards the App and reloads the service; it never touches Content. `./setup.sh --uninstall` removes only the service.

## CLI

```bash
agent-hub status                 # report deployment drift and Git state
agent-hub apply                  # deploy the Content state to agent paths
agent-hub sync                   # commit, pull with rebase, apply, push
agent-hub add-skill code-review  # create a skill from a template
agent-hub adopt ~/.claude/skills/existing-skill
```

Put global options first: `agent-hub --dry-run apply`, `agent-hub --repo PATH status`. Use `--project example-project` with `add-skill` and `adopt` for project skills.

## Web UI

Open `http://127.0.0.1:7337/`. The Web UI shows machine cards (Apply, Sync, dry-run, drift badges), Usage analytics for Claude Code and Codex (optional Grok and Cursor sources in Settings), and editors for skills, instructions, and config. The server binds to localhost and has no general-purpose authentication. To reach it from other machines on a trusted private network, see `docs/network-exposure.md`.

## Multi-machine

`config/peers.toml` maps machine IDs to Web UI base URLs under `[urls]`. The shared Peer token lives in `~/.config/agent-hub/peer-token` with mode 600, never in Git; pass it to setup with `--peer-token-file PATH` on each Machine.

## Extending

- Agents: add a section to `config/agents.toml`; define only the paths the agent supports (`{name}`, `{project}`, `{project_root}` placeholders).
- Projects: add a section to `config/projects.toml` with the path per machine.
- Skills: restrict with optional `agents` and `machines` arrays in `config/skills.toml`.
- Machines: map hostname to machine ID in `config/hub.toml`; add a peer URL to show it in the Web UI.

## Development

```bash
.venv/bin/python -m pytest       # unit, HTTP contract, and release-safety tests
node tests/web_state.mjs && node tests/web_theme.mjs
./tests/smoke.sh && ./tests/web_smoke.sh && ./tests/smoke-peers.sh
```

CI (`.github/workflows/ci.yml`) runs syntax checks (`py_compile`, `bash -n`, `node --check`), the pytest suite, the Node tests, and all three smoke suites on every supported Python version.
