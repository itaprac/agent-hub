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

For a GitHub Origin on macOS, add `--github-origin`. An SSH session cannot
always read GitHub credentials from the login Keychain, even when Sync works
in Terminal. This option uses the local `gh` login to create a write-enabled
deploy key for this repository only, verifies GitHub's host key through its
HTTPS API, and changes this Store's Origin to Git over SSH. It backs up the
local Git config. It does not copy or export the GitHub account token.

Run this option in the target's local Terminal, where `gh` can access the login
Keychain. The account needs permission to manage deploy keys for the repository.
Existing custom Git SSH commands and unknown key files cause setup to stop.

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

## Sync all configured Machines

Use the main **Sync** button on Status to sync this Machine and every configured
SSH target. The controller publishes first, then syncs targets and collects their
changes. Each Machine shows whether sync completed or is still pending. Apply,
per-Machine Sync, and dry-run remain under **Details**.

The Timer now runs the same operation every ten minutes. Run `agent-hub timer on`
once on an existing controller to update its Timer command. A sleeping target
is tried again on the next scheduled run. The controller must be running and
able to reach both the Origin and the target. Conflicts require resolution;
a scheduled run does not discard either side's edits.

From the CLI, use `agent-hub sync --all-machines`. Plain `agent-hub sync` still
operates on one Machine. Aggregate Sync does not support dry-run or `--prefer`.
