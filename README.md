# agent-hub

**One Git repository for your AI agent skills and instructions, deployed to every machine.**

[![CI](https://github.com/itaprac/agent-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/itaprac/agent-hub/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

You keep skills, instructions, and fleet config in one private Git repository (the Content repo). agent-hub deploys them to the paths that Claude Code, Codex, and other agents read. This repository (the App) is the engine: a CLI, a Web UI, and usage analytics. Python 3.11+, standard library only.

## Concepts

| Term | Meaning |
|---|---|
| App repo | This public repository: the engine, tests, and docs. |
| Content repo | Your private repository: skills, instructions, and fleet config. |
| Machine | A trusted host with a stable machine ID, mapped in `config/hub.toml`. |
| Peer | A Machine reachable from another Machine's Web UI for remote status and actions. |
| Managed block | The region between `<!-- agent-hub:begin -->` and `<!-- agent-hub:end -->` that apply rewrites. Text outside it is never touched. |

## Quick start

```bash
git clone https://github.com/itaprac/agent-hub.git && cd agent-hub
./setup.sh
```

Setup registers this Machine, installs the App into `.venv`, links a Content repo (local, cloned, or new from `example-content/`), and verifies the local Web UI. It never runs apply; review the dry-run command it prints. Every prompt has an unattended flag:

```bash
./setup.sh --new-content ../agent-hub-content --machine workstation --non-interactive
```

On macOS, setup installs the `com.agenthub.web` user service on `127.0.0.1:7337`; on Linux, it prints the foreground Web command. If the service stops after a Homebrew Python upgrade, see `docs/macos-permissions.md`.

| Command | Effect |
|---|---|
| `./setup.sh --update` | Fast-forward the App and reload the service. Never touches Content. |
| `./setup.sh --uninstall` | Remove only the service. |

## Usage

```bash
agent-hub status                 # report deployment drift and Git state
agent-hub apply                  # deploy the Content state to agent paths
agent-hub sync                   # commit, pull with rebase, apply, push
agent-hub add-skill code-review  # create a skill from a template
agent-hub adopt ~/.claude/skills/existing-skill
```

Put global options first: `agent-hub --dry-run apply`. Use `--project NAME` with `add-skill` and `adopt` for project skills.

The Web UI at `http://127.0.0.1:7337/` shows machine cards (Apply, Sync, dry-run, drift badges), usage analytics for Claude Code and Codex (Grok and Cursor optional in Settings), and editors for skills, instructions, and config. It binds to localhost and has no authentication; for access from a trusted private network, see `docs/network-exposure.md`.

## Multi-machine

`config/peers.toml` maps machine IDs to Web UI base URLs. The shared Peer token lives in `~/.config/agent-hub/peer-token` with mode 600, never in Git; pass it to setup with `--peer-token-file PATH` on each Machine.

## Configuration

These files live in your Content repo:

| File | Declares |
|---|---|
| `config/hub.toml` | Hostname to machine ID mapping. |
| `config/agents.toml` | Agents and the paths they support (`{name}`, `{project}`, `{project_root}`). |
| `config/projects.toml` | Projects, with the path per machine. |
| `config/skills.toml` | Optional `agents` and `machines` restrictions per skill. |
| `config/peers.toml` | Web UI URL per peer Machine. |

## Development

```bash
.venv/bin/python -m pytest       # unit, HTTP contract, and release-safety tests
node tests/web_state.mjs && node tests/web_theme.mjs
./tests/smoke.sh && ./tests/web_smoke.sh && ./tests/smoke-peers.sh
```

CI (`.github/workflows/ci.yml`) runs syntax checks, the pytest suite, the Node tests, and all three smoke suites on every supported Python version.

## License

MIT. See `LICENSE`.
