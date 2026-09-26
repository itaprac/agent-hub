---
status: accepted
---

# Usage across configured Machines

The operator needs API cost estimates and token totals from every configured
Machine, as in T3 Code. Reading only the Console host omits laptop activity.

This extends ADR 0004 with a read-only `usage` command over the same restricted
SSH connection. The Console uses only targets in its local `remotes.json`.
The remote CLI reads local Transcripts and never contacts its own remotes.
Each response identifies its Machine and date range. The controller validates
these fields before it adds the response to its totals.

Source switches on the controller apply to all Machines. Cursor is an account
API, so the controller reads it once. Remote reads include only Transcripts.
Token totals and cost estimates are added across Machines. Copies of the same
Transcript on different Machines can therefore count twice; the App does not
copy Transcripts between Machines.

Remote reads have a 30-second timeout and a 60-second in-process cache. An
unavailable Machine stays visible and the report is marked partial. The App
does not present an unavailable Machine as zero usage or use an old daily
window as current data. The report includes a cost and token total per Machine.

These amounts are API cost estimates, not subscription charges. Subscription
limits and billing are outside this change.

Existing restricted SSH wrappers must be updated explicitly with
`remote trust --refresh`. Refresh backs up the old wrapper and preserves the
other authorized keys. No remote installation or arbitrary command is added
to the restricted connection.
