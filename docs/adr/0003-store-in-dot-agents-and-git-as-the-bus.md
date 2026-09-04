---
status: accepted
---

# The Store is `~/.agents` and Git is the only bus between Machines

v1 kept a private Content repo with its own layout, five config files, a Web service on every Machine, and an HTTP federation with a shared token, and it relied on GitHub as the relay. Meanwhile the ecosystem (skills.sh and the agents it targets, Codex included) standardised on `~/.agents/skills` as the canonical skill store. For v2 we decided that the Store *is* the `~/.agents` directory turned into a Git repository, that every Machine learns about the others only through files committed in that repository, and that the transport is any Git remote. There is no daemon, no token, and no Machine-to-Machine network call in the core.

## Considered options

- **Keep the v1 federation and move the Content transport onto Tailscale** (Git over SSH or Git smart HTTP served by the App). Rejected: it keeps the daemon, the token, and per-Machine URLs, and it only replaces GitHub with another server the operator must run. Tailscale stays useful as a network for an SSH remote; it is not a design element.
- **Replace Git with an App-level file sync over the Peer API.** Rejected: it reinvents history, conflicts, and atomic changes, and it contradicts ADR 0002.
- **Own layout under a separate Content repo, as in v1.** Rejected: every skills.sh install would have to be adopted into the Store, and the App would keep its own Agent path table instead of the one the ecosystem maintains.
- **Rewrite in TypeScript on top of the skills CLI.** Deferred: the value of v2 is the model, not the language. The Python core (Managed block, Git plumbing, Sync, editor, Console) carries over. Revisit if `npx agent-hub` adoption becomes the goal.

## Consequences

- skills.sh is the installer for third-party Skills; the App commits, filters, links, and syncs. Node is optional and only needed for `install` and `update`.
- The Agent table is vendored from skills.sh (MIT) and extended with instruction-file paths. Agents split into Universal agents, which read `.agents/skills` natively, and the rest, which get a symlink. `agents.toml` disappears.
- Fleet state is a Machine record committed on Sync. Freshness is bounded by the Timer interval, so remote "Apply" and "Sync" buttons go away.
- Projects are not the Store's concern: project Skills and instructions travel in the project repository. The Store may hold private Project skills for public projects, linked locally and excluded through `.git/info/exclude`.
- Removed from v1: `hub.toml [machines]`, `agents.toml`, `projects.toml`, `peers.toml`, the Peer token, `/api/peers`, cross-Machine Usage, the App service as a requirement, and `setup.sh` in favour of a packaged install.
