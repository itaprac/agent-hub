# agent-hub

The source-of-truth console for agent skills, instructions, machine state, and synchronization across trusted machines.

## Language

**App repo**:
The public repository that holds the engine: the hub, the web console, usage analytics, tests, and all documentation. Contains no personal data.
_Avoid_: engine repo, code repo

**Content repo**:
The private repository that holds what the engine manages: skills, instructions, and the fleet config. Git is its database. Synchronized across machines by the hub.
_Avoid_: data repo, config repo

**Machine**:
A trusted host in the fleet. Its stable machine ID remains the same even if its hostname changes.
_Avoid_: host (when referring to fleet identity), device

**Peer**:
A Machine that another Machine can contact through agent-hub for remote status and actions. Peer is a relationship between Machines, not a separate kind of Machine.
_Avoid_: node, server

**App service**:
The launchd user service `com.agenthub.web` that runs the installed Web command on localhost. Setup alone manages its install, update, and uninstall.
_Avoid_: daemon, web service

**Fleet config**:
The declaration of every trusted machine, project, peer, and skill assignment. Describes the whole fleet, not one machine. Lives in the Content repo.
_Avoid_: settings, local config

**Managed block**:
The region of an instruction file that the hub owns and rewrites on apply. Bounded by a begin and an end marker; its first line is a do-not-edit notice. Everything outside the block belongs to the operator and is never touched.
_Avoid_: managed section, hub block

**Marker**:
The fixed line that bounds a managed block. Matched exactly, so its text never changes; anything meant for humans lives inside the block, not in the marker.

**Apply**:
The operation that brings this Machine's agent paths to the state declared in the Content repo: links or copies Skills, rewrites Managed blocks, prunes stale links. Never touches operator content outside a Managed block.
_Avoid_: deploy (as a command name), install

**Sync**:
The operation that commits local Content changes, pulls with rebase, applies, and pushes. Touches only the Content repo and this Machine's agent paths.
_Avoid_: refresh, update (for Content)

**Update**:
The App-only refresh: `setup.sh --update` fast-forwards the App repo, reinstalls the environment, and reloads the App service. Never reads or writes Content beyond the pointer.
_Avoid_: upgrade, sync (for the App)

**Skill directory**:
A directory directly under `skills/` whose name does not start with a dot and that contains at least one non-hidden file (no part of the file's path inside the skill starts with a dot). Empty and hidden directories are invisible to the hub and the console. Listed in case-insensitive name order.
_Avoid_: skill folder

**Vendored skill**:
A third-party skill copied into the content repo for local use. Lives only in the private content repo; the app repo ships none.
_Avoid_: bundled skill

**Example content**:
The minimal hand-written skill and instruction that ship with the app repo to show the expected shape of a content repo. Not managed content.

**Usage source**:
An external agent tool whose activity the usage analytics reads: Claude Code, Codex, Grok, or Cursor. Claude Code, Codex, and Grok are scanned from transcript directories under the operator's home; Cursor is fetched over HTTP.
_Avoid_: provider, integration

**Transcript**:
One session JSONL file written by a usage source. The raw material of usage analytics; never written by the hub.
_Avoid_: log, history file

**Usage snapshot**:
The in-process, TTL-bounded cache of the aggregated usage result. Disposable: rebuilt from transcripts at any time, lost on restart by design.
_Avoid_: usage database, index

**Colour scheme**:
The operator's chosen appearance for the web console. One of Dark, Light, Black, or System.
_Avoid_: theme (in UI copy)

**Dark**:
The designed dim appearance of the web console. Warm olive ink, not black.
_Avoid_: AMOLED, OLED, Black (for this appearance)

**Light**:
The designed paper appearance of the web console. Warm olive paper.

**Black**:
The near-black appearance of the web console. Page and rail canvas are black. Cards and recessed surfaces stay slightly lighter and keep the olive tint. The operator picks it explicitly.
_Avoid_: AMOLED, OLED, Dark (for this appearance)

**System**:
The colour scheme that follows the OS light or dark appearance. OS dark maps to Dark, never to Black.
