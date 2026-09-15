---
status: accepted
---

# One Sync action for configured Machines

The Console's primary Sync action includes this Machine and all SSH targets
explicitly configured on its controller. Apply and per-Machine commands remain
in Details. This extends ADR 0004 to scheduled remote Sync as well as manual
remote control. Git remains the transport for content and Machine records.

The controller syncs first, even if a target is asleep. Each reachable target
then syncs, and the controller syncs again to collect content and records.
If a later target introduced content, earlier successful targets that are now
Behind get one reconciliation pass. The final records must confirm convergence.
Errors and timeouts do not get immediate retries. Conflicts never select a side.
A pending push cannot count as successful synchronization, even if Apply passed.

The existing Timer runs `sync --all-machines` every ten minutes. Each scheduled
run makes a fresh identity check before remote changes. This lets a sleeping
Machine catch up after it becomes reachable, without an open browser or a new
background service. The controller must be running. Existing Timer installations
need `agent-hub timer on` once to update their command. Unconfigured Machines
remain visible and cannot receive remote commands.

The last aggregate result is local metadata in the Store's Git directory.
It survives Console restarts and is not committed or sent through the Origin.
A file lock prevents concurrent aggregate runs from the Timer and Console.
Status includes uncommitted changes, pending Git commits, and per-Machine results.
Machine records remain the last known state, not a live reachability claim.
