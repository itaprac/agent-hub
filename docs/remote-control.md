# Control a Machine from another Console

The controller can start Sync or Apply on a target Machine through SSH.
Both Machines need a Store and the agent-hub CLI. The target does not need a
running Console. Use Tailscale on both Machines for private reachability.
On a Mac target, enable System Settings > General > Sharing > Remote Login
for the account that owns the Store.

## Pair the Machines

Create a dedicated Ed25519 key on the controller. Keep the private key there.
Use an existing SSH session or verify the target host key before enabling
remote actions. Runtime connections require an entry in `~/.ssh/known_hosts`.

Install the current CLI on the target, then run:

```sh
agent-hub remote trust --controller CONTROLLER_TAILSCALE_IP --public-key 'ssh-ed25519 PUBLIC_KEY'
```

This backs up existing SSH keys and adds a restricted authorization. It permits
only Status, Apply, and Sync on the selected Store, from that controller's
Tailscale address. Other SSH keys remain intact. Use `--store` for a custom
Store and `--executable` for a different agent-hub entry point.

On the controller, create `~/.config/agent-hub/remotes.json`, with mode `0600`:

```json
{
  "laptop": {
    "destination": "user@100.100.100.100",
    "executable": "/Users/user/.local/bin/agent-hub",
    "store": "/Users/user/.agents",
    "identity_file": "/Users/controller/.ssh/agent-hub-laptop"
  }
}
```

The table key must match the target's pinned Machine ID. Paths must be absolute.
The target must have synced once so its record exists in Fleet. Refresh the
controller Console; Sync and Apply appear on that Machine. Config is read on
each request, so no Console restart is needed to add a target.

## Run commands

Remote Sync publishes the controller Store, runs Sync on the target, and pulls
its record back. Remote Apply only applies files already on the target; use
Sync to fetch new content. A remote dry-run performs no local Sync.
The log names the Machine for each stage. It distinguishes a completed remote
Sync from a failed controller refresh.

An unreachable or sleeping target reports an SSH error. The App does not wake
it or queue a command for later. A timeout can leave the remote process running;
check its state before retrying. Git conflicts require normal local resolution.
Remote buttons do not offer `--prefer` or arbitrary shell commands.

To revoke access, remove the line marked `agent-hub:` for this key from the
target's `~/.ssh/authorized_keys`, then remove its controller config entry.
If an App or Python update changes the executable paths, revoke and pair again.
