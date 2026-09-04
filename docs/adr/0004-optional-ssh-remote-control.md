---
status: accepted
---

# Optional Console actions over SSH

The operator wants to start Sync on a MacBook from the Console on a Mac mini.
Waiting for its Timer or opening a second Console does not meet this need.
This amends ADR 0003, which removed remote actions from v2.

The Console can run Apply and Sync on explicitly configured SSH targets.
Configuration lives in `~/.config/agent-hub/remotes.json` on the controller,
outside the shared Store. A Store commit cannot enable remote command access.
The browser sends only a Machine ID, an allowed command, and a dry-run flag.
It cannot supply an SSH address, executable, or shell command.

SSH uses strict host-key checks and batch authentication. A status preflight
checks the target Machine ID before any remote mutation. The optional
`remote trust` command adds a dedicated key with a forced command limited to
Status, Apply, and Sync on one Store. It restricts the source to one Tailscale
address and disables forwarding and interactive sessions.

Remote Sync first synchronizes the controller Store, then runs Sync on the
target, then synchronizes the controller again to read the target's record.
This publishes pending edits before the target pulls them. Errors identify
which stage failed. Remote Apply and remote dry-run do not run local Sync.
Commands are serialized with the Console's other Store operations.

No HTTP service or App daemon is required on the target. On macOS, the built-in
Remote Login service must be enabled. SSH can use the existing Tailscale network.
Content, conflicts, history, and Fleet records still travel through Git.
Fleet freshness is not a claim that a Machine is currently reachable.

Timeouts do not trigger retries: the remote process may still be running.
The Console remains limited to trusted private networks, as described in
`docs/network-exposure.md`. SSH access does not add browser authentication.
