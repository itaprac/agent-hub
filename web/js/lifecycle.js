// Disable, enable, and delete Skills and instructions; instruction backups.

import { api } from "./api.js";
import { diffHunks, diffLines } from "./diff.js";
import { el, formatBytes, toast } from "./dom.js";
import { choiceDialog, confirmDialog, formDialog } from "./modals.js";
import { editSkillTargets, targetSummary } from "./targets.js";

const DISABLED = "disabled/";

const enabledPath = (path) => (path.startsWith(DISABLED) ? path.slice(DISABLED.length) : path);

function findSkill(state, path) {
  const scopes = [[null, state.skills?.global || []]];
  for (const [project, skills] of Object.entries(state.skills?.projects || {})) scopes.push([project, skills]);
  for (const [project, skills] of scopes) {
    const skill = skills.find((item) => path === item.path || path.startsWith(`${item.path}/`));
    if (skill) return { skill, project };
  }
  return null;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function diffView(before, after) {
  const hunks = diffHunks(diffLines(before, after));
  const changed = hunks.filter((line) => line.op === "+" || line.op === "-");
  if (!changed.length) return el("div", { class: "modal-text", text: "The backup is the same as the text in the editor." });
  const removed = changed.filter((line) => line.op === "-").length;
  const added = changed.length - removed;
  return el("div", { class: "diff-wrap" }, [
    el("div", { class: "diff-summary" }, [
      el("span", { class: "diff-del", text: `−${removed} only in backup` }),
      el("span", { class: "diff-add", text: `+${added} only in editor` }),
    ]),
    el("pre", { class: "diff", tabIndex: 0, "aria-label": "Line changes from the backup to the editor text" },
      hunks.map((line) => line.op === "gap"
        ? el("span", { class: "diff-line diff-gap", text: `… ${line.count} unchanged line${line.count === 1 ? "" : "s"}` })
        : el("span", {
          class: `diff-line${line.op === "-" ? " diff-del" : line.op === "+" ? " diff-add" : ""}`,
          text: `${line.op} ${line.text}`,
        }))),
  ]);
}

export function createLifecycle({ runHub, refresh }) {
  // Moves the open file with its item, so the editor keeps showing it.
  async function toggle(helpers, label, invoke, path, off) {
    if (!(await helpers.editor.confirmDiscard())) return;
    const result = await runHub(label, invoke);
    // Apply may report unrelated problems; the move line says whether the file moved.
    if (result?.lines?.some((line) => line.level === "ok" && line.text.startsWith(off ? "enabled" : "disabled"))) {
      await helpers.openPath(off ? enabledPath(path) : `${DISABLED}${path}`);
    }
  }

  function skillContext(path, state, helpers) {
    const found = findSkill(state, path);
    if (!found) return null;
    const { skill, project } = found;
    const off = Boolean(skill.disabled);
    const scope = project ? `project ${project}` : "global";
    const count = skill.files.length;
    return {
      label: skill.name,
      off,
      note: off ? `${scope} · not deployed to agents` : `${scope} · ${targetSummary(state, skill.name)}`,
      actions: [
        {
          label: "Targets",
          title: "Choose the Machines and Agents that get this Skill",
          run: () => editSkillTargets(state, skill.name, runHub),
        },
        {
          label: off ? "Enable" : "Disable",
          title: off ? "Move the Skill back to skills/ and apply" : "Move the Skill to disabled/ and apply; you can enable it again",
          kind: off ? "primary" : "",
          run: () => toggle(
            helpers,
            `${off ? "enable" : "disable"} ${skill.name}`,
            () => api.skillAction(off ? "enable" : "disable", skill.name, project),
            path,
            off,
          ),
        },
        {
          label: "Delete skill",
          kind: "danger",
          run: async () => {
            const ok = await confirmDialog({
              title: `Delete ${skill.name}?`,
              body: `This removes the Skill directory and its ${count} file${count === 1 ? "" : "s"}. `
                + "Apply removes it from the agents on this Machine; Sync removes it on the other Machines. "
                + "Git history keeps the versions that were committed. To keep the Skill, use Disable instead.",
              confirmLabel: "Delete skill",
              danger: true,
            });
            if (!ok || !(await helpers.editor.confirmDiscard())) return;
            const result = await runHub(`delete ${skill.name}`, () => api.skillAction("delete", skill.name, project));
            if (result?.exit_code === 0) await helpers.openPath(null);
          },
        },
      ],
    };
  }

  async function createBackup(entry, helpers) {
    const dirty = helpers.editor.isDirty();
    const values = await formDialog({
      title: `Back up ${entry.name}`,
      sub: dirty
        ? "The backup copies the saved file. Your unsaved edits are not in it; save first to include them."
        : "Saves a copy of the file. You can compare with it and restore it later.",
      confirmLabel: "Back up",
      fields: [{ name: "label", label: "Label (optional)", placeholder: "before trimming the review rules" }],
    });
    if (!values) return;
    try {
      await api.createBackup(entry.path, values.label);
      toast(`backed up ${entry.name}`, "ok", 2600);
      await refresh();
    } catch (error) {
      toast(`backup failed: ${error.message}`, "err", 7000);
    }
  }

  async function restoreBackup(entry, backup, helpers) {
    if (!(await helpers.editor.confirmDiscard())) return false;
    try {
      const result = await api.restoreBackup(entry.path, backup.id, helpers.editor.revision());
      await helpers.editor.reload();
      const applied = result.apply?.exit_code === 0;
      toast(applied ? `restored ${entry.name} and applied it` : `restored ${entry.name}; Apply reported problems`, applied ? "ok" : "err", 4200);
      await refresh();
      return true;
    } catch (error) {
      toast(`restore failed: ${error.message}`, "err", 7000);
      return false;
    }
  }

  async function deleteBackup(entry, backup) {
    try {
      await api.deleteBackup(entry.path, backup.id);
      toast("backup deleted", "ok", 2400);
      await refresh();
      return true;
    } catch (error) {
      toast(`delete failed: ${error.message}`, "err", 7000);
      return false;
    }
  }

  async function compareBackup(entry, backup, helpers) {
    let file;
    try {
      file = await api.readFile(backup.path);
    } catch (error) {
      toast(`open failed: ${error.message}`, "err", 7000);
      return;
    }
    const choice = await choiceDialog({
      title: `Compare with backup`,
      sub: `${formatDate(backup.created)}${backup.label ? ` · ${backup.label}` : ""}`,
      wide: true,
      body: () => [diffView(file.content, helpers.editor.text())],
      buttons: [
        { label: "Close", value: null },
        { label: "Restore this backup", value: "restore", kind: "primary" },
      ],
    });
    if (choice === "restore") await restoreBackup(entry, backup, helpers);
  }

  async function showBackups(entry, backups, helpers) {
    const choice = await choiceDialog({
      title: `Backups of ${entry.name}`,
      sub: "Restore writes the backup to the file and applies it to the agents on this Machine.",
      wide: true,
      body: (choose) => [
        el("div", { class: "backup-list", role: "list" }, backups.map((backup) =>
          el("div", { class: "backup-row", role: "listitem" }, [
            el("div", { class: "backup-meta" }, [
              el("span", { class: "backup-date", text: formatDate(backup.created) }),
              el("span", { class: "backup-label", text: backup.label || "no label" }),
            ]),
            el("span", { class: "backup-size", text: formatBytes(backup.size) }),
            el("button", { type: "button", class: "btn btn-sm", text: "Compare", onClick: () => choose({ action: "compare", backup }) }),
            el("button", { type: "button", class: "btn btn-sm", text: "Restore", onClick: () => choose({ action: "restore", backup }) }),
            el("button", {
              type: "button", class: "btn btn-sm btn-danger", text: "Delete",
              "aria-label": `Delete backup from ${formatDate(backup.created)}`,
              onClick: () => choose({ action: "delete", backup }),
            }),
          ]))),
      ],
      buttons: [{ label: "Close", value: null }],
    });
    if (!choice) return;
    if (choice.action === "compare") await compareBackup(entry, choice.backup, helpers);
    else if (choice.action === "restore") await restoreBackup(entry, choice.backup, helpers);
    else if (choice.action === "delete") {
      const ok = await confirmDialog({
        title: "Delete backup?",
        body: `The backup from ${formatDate(choice.backup.created)} will be removed.`,
        confirmLabel: "Delete backup",
        danger: true,
      });
      if (ok) await deleteBackup(entry, choice.backup);
    }
  }

  function instructionContext(path, state, helpers) {
    const entry = (state.instructions?.global || []).find((item) => item.path === path);
    if (!entry || !entry.exists) return null;
    const off = Boolean(entry.disabled);
    const source = entry.source || entry.path;
    const backups = state.backups?.[source] || [];
    const base = source === "AGENTS.md";
    return {
      label: entry.name,
      off,
      note: off
        ? "not in the Managed block"
        : base ? "shared by every Agent" : `Overlay for ${entry.name.replace(/\.md$/, "")}`,
      actions: [
        {
          label: off ? "Enable" : "Disable",
          title: off ? "Put the file back into the Managed block and apply" : "Remove the file from the Managed block and apply; you can enable it again",
          kind: off ? "primary" : "",
          run: () => toggle(
            helpers,
            `${off ? "enable" : "disable"} ${entry.name}`,
            () => api.instructionAction(off ? "enable" : "disable", path),
            path,
            off,
          ),
        },
        { label: "Back up", title: "Save a copy of this file", run: () => createBackup(entry, helpers) },
        {
          label: `Backups (${backups.length})`,
          title: backups.length ? "Compare, restore, or delete backups" : "No backups yet",
          run: () => (backups.length ? showBackups(entry, backups, helpers) : toast("no backups yet; use Back up first", "info", 3000)),
        },
      ],
    };
  }

  return { skillContext, instructionContext };
}
