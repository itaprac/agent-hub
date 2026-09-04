# agent-hub

The console and CLI that keep agent Skills and instructions the same on every trusted Machine.

## Language

### Store and content

**App repo**:
The public repository that holds the engine: the CLI, the Console, usage analytics, tests, and all documentation. Contains no personal data.
_Avoid_: engine repo, code repo

**Store**:
The `~/.agents` directory as a Git repository. Holds Skills, instructions, Project skills, and Machine records. Git is its database. Synchronized between Machines through the Origin.
_Avoid_: content repo, data repo, config repo

**Origin**:
The Git remote of the Store. Any Git host or an SSH path to one of your own Machines. Never a required part of the App.
_Avoid_: server, hub machine, relay

**Skill**:
A directory directly under `skills/` whose name does not start with a dot and that contains at least one non-hidden file, with a `SKILL.md` per the Agent Skills specification. Listed in case-insensitive name order.
_Avoid_: skill folder

**Installed skill**:
A Skill that skills.sh added to the Store and recorded in `.skill-lock.json`. The App shows its source and updates it through skills.sh; it never installs one itself.
_Avoid_: vendored skill, third-party skill, bundled skill

**Overlay**:
The optional per-Agent instruction file `agents/<agent-id>.md`, rendered after `AGENTS.md` inside that Agent's Managed block.
_Avoid_: agent instructions, agent file

**Project skill**:
A private Skill kept in the Store under `projects/<slug>/skills/` and linked into one project checkout without being committed there. For projects whose own repository must not carry it.
_Avoid_: project config, local skill

**Project slug**:
The stable name derived from a project's origin URL. Identifies a project across Machines without a path table.
_Avoid_: project ID, project path

### Agents

**Agent**:
A coding agent tool with known skill and instruction paths, such as Claude Code or Codex. Described by one row of the Agent table.
_Avoid_: harness, tool, provider, adapter

**Agent table**:
The vendored list of Agents and their paths, taken from skills.sh and extended with instruction-file paths. The only place that knows where an Agent looks.
_Avoid_: agents.toml, adapter config

**Universal agent**:
An Agent that reads `.agents/skills` natively and therefore gets no skill link, only a Managed block.
_Avoid_: native agent, direct agent

**Detected agent**:
An Agent whose home directory exists on this Machine. Apply targets detected Agents unless the operator lists Agents explicitly.
_Avoid_: installed agent, active agent

### Machines and fleet

**Machine**:
A trusted host with a stable Machine ID. The ID defaults to the short hostname and can be pinned in a local file so a hostname change does not change identity.
_Avoid_: host (for fleet identity), device, node, peer

**Machine record**:
The small JSON file `machines/<machine-id>.json` that a Machine commits on Sync: its Agents, the Store commit it applied, its problems, and when it last synced.
_Avoid_: heartbeat, peer status, machine state

**Fleet**:
Every Machine with a Machine record in the Store. Seen only through the Store; never contacted over the network.
_Avoid_: cluster, peers

**Current**:
A Machine whose Machine record points at, or past, the last content commit in the Store.
_Avoid_: in sync (for one Machine), up to date

**Behind**:
A Machine whose Machine record predates the last content commit. Measured in content commits.
_Avoid_: stale (for a Machine), outdated

### Operations

**Managed block**:
The region of an instruction file that the App owns and rewrites on Apply. Bounded by a begin and an end marker; its first line is a do-not-edit notice. Everything outside the block belongs to the operator and is never touched.
_Avoid_: managed section, hub block

**Marker**:
The fixed line that bounds a Managed block. Matched exactly, so its text never changes; anything meant for humans lives inside the block, not in the marker.

**Apply**:
The operation that brings this Machine's Agent paths to the state declared in the Store: links or copies Skills, rewrites Managed blocks, prunes stale links, links Project skills. Never touches operator content outside a Managed block.
_Avoid_: deploy, install (for Skills from the Store)

**Sync**:
The operation that commits Store changes, pulls with rebase, applies, writes the Machine record, and pushes. Continues without the Origin when it is unreachable and reports the pending push.
_Avoid_: refresh, update (for the Store)

**Timer**:
The optional user scheduler job (launchd or systemd) that runs Sync on an interval. The only background process the App knows about.
_Avoid_: daemon, service, watcher

**Console**:
The local web UI started on demand by `agent-hub ui`, optionally kept running as a user service. Shows the Fleet, status, and editors.
_Avoid_: web service, dashboard, App service

**Conflict**:
A Sync stopped because the same file changed on two Machines. The Store is left as it was before the pull; the operator resolves by preferring one side.
_Avoid_: merge error, rebase failure

### Usage analytics

**Usage source**:
An external agent tool whose activity the usage analytics reads on this Machine: Claude Code, Codex, Grok, or Cursor.
_Avoid_: provider, integration

**Transcript**:
One session JSONL file written by a Usage source. The raw material of usage analytics; never written by the App.
_Avoid_: log, history file

**Usage snapshot**:
The in-process, TTL-bounded cache of the aggregated usage result. Disposable: rebuilt from Transcripts at any time, lost on restart by design.
_Avoid_: usage database, index

### Appearance

**Colour scheme**:
The operator's chosen appearance for the Console. One of Dark, Light, Black, or System.
_Avoid_: theme (in UI copy)

**Dark**:
The designed dim appearance of the Console. Warm olive ink, not black.
_Avoid_: AMOLED, OLED, Black (for this appearance)

**Light**:
The designed paper appearance of the Console. Warm olive paper.

**Black**:
The near-black appearance of the Console. Page and rail canvas are black. Cards and recessed surfaces stay slightly lighter and keep the olive tint. The operator picks it explicitly.
_Avoid_: AMOLED, OLED, Dark (for this appearance)

**System**:
The colour scheme that follows the OS light or dark appearance. OS dark maps to Dark, never to Black.
