# agent-hub

Keep AI agent Skills and instructions in one Git repository, and apply them on each Machine.

The Store is `~/.agents`. It contains `skills/`, `AGENTS.md`, optional `agents/<agent-id>.md` overlays, private Project skills, and Machine records. The App provides a CLI, a local Console, and local Usage analytics. Python 3.11+ is required. Node is needed only for skills.sh install and update commands.

## Start on the first Machine

```sh
uv tool install git+https://github.com/itaprac/agent-hub.git
agent-hub init --yes
agent-hub --dry-run apply
agent-hub apply
```

You can use `pipx install git+https://github.com/itaprac/agent-hub.git` instead. Init keeps existing Skills and instructions. It selects detected Agents; Apply creates relative skill links and writes Managed blocks. Text outside those blocks stays unchanged.

To sync through an existing empty Git remote, use `agent-hub init --remote git@example.com:you/agents.git --yes`, then `agent-hub sync`. The Origin can be any Git host or an SSH path to a Machine you control.

## Add a second Machine

```sh
uv tool install git+https://github.com/itaprac/agent-hub.git
agent-hub init --from git@example.com:you/agents.git --yes
agent-hub apply
agent-hub timer on
```

Run `agent-hub timer on` on each Machine that should sync automatically. The optional Timer uses launchd on macOS or systemd on Linux. `agent-hub timer off` removes it. See [macOS permissions](docs/macos-permissions.md) if a background process cannot read a protected directory.

## Commands

| Command | Effect |
|---|---|
| `agent-hub status` | Check local links, instructions, and Git state. |
| `agent-hub status --fleet` | Read Machine records from the Store. |
| `agent-hub apply` | Apply Skills and Managed blocks locally; use `--copy` for copies. |
| `agent-hub sync` | Commit, pull with rebase, apply, record this Machine, and push. |
| `agent-hub sync --prefer local` | Resolve a content conflict with the local version; `remote` selects the other version. |
| `agent-hub install owner/repo --skill NAME` | Use skills.sh to install a Skill, then apply and commit it. |
| `agent-hub update [NAME ...]` | Use skills.sh to update installed Skills, then apply and commit. |
| `agent-hub add-skill NAME` | Create a Skill in the Store. |
| `agent-hub adopt PATH` | Move an existing Skill into the Store and leave a link. |
| `agent-hub project link PATH` | Link private Project skills into a checkout and exclude them from Git. |
| `agent-hub add-skill NAME --project PATH` | Create a private Skill for that checkout. |
| `agent-hub adopt PATH --project` | Adopt a Skill for its containing checkout. |
| `agent-hub timer on\|off\|status` | Manage automatic Sync. |
| `agent-hub ui` | Print the local Console URL and run in the foreground. |
| `agent-hub ui --service on\|off\|status` | Manage the optional Console user service. |
| `agent-hub migrate PATH` | Migrate a clean v1 Content repository to the Store layout. |

Global options go first, for example `agent-hub --dry-run apply` or `agent-hub --store /path/to/store status`. `AGENT_HUB_STORE` also selects the Store. The Machine ID defaults to the short hostname; pin it in `~/.config/agent-hub/machine` if needed.

## Optional configuration

With no `hub.toml`, Apply uses detected Agents and all Skills. To select Agents or restrict a Skill, create `~/.agents/hub.toml`:

```toml
[agents]
enabled = ["claude-code", "codex"]
mode = "symlink"

[skills.review]
agents = ["claude-code"]
machines = ["workstation"]
```

The Console edits Skills, instructions, overlays, and `hub.toml`. It shows installed Skill sources and Fleet freshness from Git records. It reads Usage only on the local Machine. It binds to `127.0.0.1:7337` and has no authentication. See [network exposure](docs/network-exposure.md) for access through a trusted private network.

## Development

Run `python3 -m pip install -e '.[dev]'`, then `python3 -m pytest`, `node tests/web_state.mjs`, `node tests/web_theme.mjs`, `./tests/smoke.sh`, and `./tests/web_smoke.sh`. Tests use temporary Stores and HOME directories.

MIT. See [LICENSE](LICENSE).
