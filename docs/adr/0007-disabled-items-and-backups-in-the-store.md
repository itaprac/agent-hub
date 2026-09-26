---
status: accepted
---

# Disabled items and backups are files in the Store

The operator can disable a Skill, `AGENTS.md`, or an Overlay and enable it
again later. A disabled item moves to the same relative path under `disabled/`
in the Store, for example `skills/x` to `disabled/skills/x`. Apply deploys only
the enabled paths, so it prunes the links of a disabled Skill. Sync commits the
move, so the item is disabled on every Machine. When `AGENTS.md` is disabled,
Apply keeps an empty Managed block, or a block with only the enabled Overlay,
so that the old text does not stay in the Agent files.

For a Skill installed by skills.sh, its lock entry moves to
`disabled/.skill-lock.json`. Otherwise `skills update` could install the Skill
again. Enable moves the entry back.

A backup is a copy of one file under `backups/<path>/<timestamp>[--label].md`.
The key is the enabled path, so the backups of a file stay with it while it is
disabled. Restore writes the backup to the file with the usual revision check,
then runs Apply.

## Considered options

- **A flag in `hub.toml`.** Rejected: Universal agents read `~/.agents/skills`
  directly, so a Skill that stays in `skills/` stays active for them.
- **`skills/.disabled/`.** Rejected: some Agents search `skills/` recursively.
- **Use only Git history as the backup.** Rejected: the operator wants a copy
  with a name that is simple to compare with and restore, before the next Sync.

## Consequences

- `disabled/` and `backups/` are Store areas that the Console can read and edit.
- Delete removes a Skill directory. Git history keeps only committed versions.
