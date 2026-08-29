// Reusable file editor pane: load, dirty tracking, save (Cmd+S), revert, delete.

import { api } from "./api.js";
import { clear, el, formatBytes, splitPath, toast } from "./dom.js";
import { confirmDialog, conflictDialog } from "./modals.js";

let editorSequence = 0;

export function createEditor({ onSaved, onDeleted, onDirty, emptyTitle = "Pick a file", emptyBody = "Select a file in the tree to edit it here." } = {}) {
  const editorId = ++editorSequence;
  let current = null; // {path, exists, revision}
  let baseline = "";
  let busy = false;

  const pathLabel = el("span", { class: "editor-path" });
  const flag = el("span", { class: "editor-flag", hidden: true });
  const revertButton = el("button", { class: "btn", text: "Revert", onClick: revert, disabled: true });
  const deleteButton = el("button", { class: "btn btn-danger", text: "Delete", onClick: remove, disabled: true });
  const saveButton = el("button", { class: "btn btn-primary", text: "Save", onClick: save, disabled: true });

  const textareaId = `file-editor-${editorId}`;
  const textareaLabel = el("label", { class: "sr-only", for: textareaId, text: "File editor" });
  const textarea = el("textarea", {
    id: textareaId,
    autocapitalize: "off",
    autocorrect: "off",
    disabled: true,
  });
  textarea.setAttribute("spellcheck", "false");
  textarea.setAttribute("wrap", "soft");

  const position = el("span", { text: "Ln 1, Col 1" });
  const size = el("span", { text: "" });
  const editorStatus = el("span", {
    class: "sr-only",
    role: "status",
    "aria-live": "polite",
    "aria-atomic": "true",
  });

  const head = el("div", { class: "editor-head" }, [
    pathLabel,
    flag,
    el("span", { class: "spacer" }),
    revertButton,
    deleteButton,
    saveButton,
    editorStatus,
  ]);
  const body = el("div", { class: "editor-body" }, [textareaLabel, textarea]);
  const foot = el("div", { class: "editor-foot" }, [position, el("span", { class: "spacer" }), size, el("span", { text: "⌘S saves" })]);
  const placeholder = el("div", { class: "editor-placeholder" }, [
    el("strong", { text: emptyTitle }),
    el("span", { text: emptyBody }),
    el("span", { class: "editor-keys", text: "/ filters the tree · ⌘S saves · R refreshes" }),
  ]);

  const element = el("div", { class: "editor" }, [head, placeholder]);

  textarea.addEventListener("input", refreshFlags);
  textarea.addEventListener("keyup", refreshPosition);
  textarea.addEventListener("click", refreshPosition);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.setRangeText("  ", start, end, "end");
      refreshFlags();
    }
  });

  function isDirty() {
    return Boolean(current) && textarea.value !== baseline;
  }

  function announce(message) {
    editorStatus.textContent = "";
    requestAnimationFrame(() => {
      editorStatus.textContent = message;
    });
  }

  function refreshPosition() {
    const upto = textarea.value.slice(0, textarea.selectionStart);
    const lines = upto.split("\n");
    position.textContent = `Ln ${lines.length}, Col ${lines[lines.length - 1].length + 1}`;
  }

  function refreshFlags() {
    const dirty = isDirty();
    saveButton.disabled = busy || !current || (!dirty && current.exists);
    saveButton.textContent = current && !current.exists ? "Create" : "Save";
    revertButton.disabled = busy || !dirty;
    deleteButton.disabled = busy || !current || !current.exists;
    flag.hidden = !current || (!dirty && current.exists);
    if (!flag.hidden) {
      const isNew = current && !current.exists;
      flag.className = `editor-flag ${isNew ? "new" : "dirty"}`;
      flag.textContent = isNew ? (dirty ? "new · unsaved" : "new file") : "unsaved";
    }
    size.textContent = current ? formatBytes(new TextEncoder().encode(textarea.value).length) : "";
    refreshPosition();
    if (onDirty) onDirty(isDirty());
  }

  function showPane(hasFile) {
    clear(element);
    element.append(head);
    element.append(hasFile ? body : placeholder);
    if (hasFile) element.append(foot);
  }

  function setPath(path) {
    clear(pathLabel);
    if (!path) {
      pathLabel.append(document.createTextNode("—"));
      pathLabel.title = "";
      textareaLabel.textContent = "File editor";
      return;
    }
    const [dir, name] = splitPath(path);
    if (dir) pathLabel.append(el("span", { class: "dir", text: dir }));
    pathLabel.append(document.createTextNode(name));
    pathLabel.title = path;
    textareaLabel.textContent = `File editor for ${path}`;
  }

  async function confirmDiscard() {
    if (!isDirty()) return true;
    return confirmDialog({
      title: "Discard unsaved changes?",
      body: `${current.path} has unsaved edits.`,
      confirmLabel: "Discard",
      danger: true,
    });
  }

  async function open(path, { exists = true, template = "" } = {}) {
    if (busy) return false;
    if (current && current.path === path && !isDirty()) return true;
    if (!(await confirmDiscard())) return false;

    let content = template;
    let revision = null;
    if (exists) {
      try {
        const file = await api.readFile(path);
        content = file.content;
        revision = file.revision;
      } catch (error) {
        announce(`Could not open ${path}`);
        toast(`open failed: ${error.message}`, "err");
        return false;
      }
    }
    current = { path, exists, revision };
    baseline = content;
    textarea.value = content;
    textarea.disabled = false;
    setPath(path);
    showPane(true);
    refreshFlags();
    textarea.focus();
    // Assigning .value parks the caret at the end of the text; put it back at the
    // top so the pane opens on line 1 and the footer agrees with the caret.
    textarea.setSelectionRange(0, 0);
    textarea.scrollTop = 0;
    refreshPosition();
    announce(`Opened ${path}`);
    return true;
  }

  async function close({ force = false } = {}) {
    if (busy && !force) return false;
    if (!force && !(await confirmDiscard())) return false;
    current = null;
    baseline = "";
    textarea.value = "";
    textarea.disabled = true;
    setPath("");
    showPane(false);
    refreshFlags();
    return true;
  }

  function revert() {
    if (!current) return;
    textarea.value = baseline;
    refreshFlags();
    textarea.focus();
    announce(`Reverted unsaved changes in ${current.path}`);
  }

  async function readLatest(path) {
    try {
      return await api.readFile(path);
    } catch (error) {
      if (error.status === 404) return null;
      throw error;
    }
  }

  function showLatest(path, file) {
    const exists = Boolean(file);
    const content = file ? file.content : "";
    current = { path, exists, revision: file ? file.revision : null };
    baseline = content;
    textarea.value = content;
    refreshFlags();
    textarea.focus();
    textarea.setSelectionRange(0, 0);
    textarea.scrollTop = 0;
    announce(file ? `Loaded latest ${path}` : `${path} no longer exists`);
  }

  async function finishSave(path, content, result) {
    baseline = content;
    current = { path, exists: true, revision: result.revision };
    refreshFlags();
    announce(`${result.created ? "Created" : "Saved"} ${path}`);
    toast(`${result.created ? "created" : "saved"} ${path}`, "ok", 2600);
    if (onSaved) await onSaved(path, result);
    return true;
  }

  async function resolveSaveConflict(path, content) {
    announce(`Save conflict for ${path}`);
    const choice = await conflictDialog({
      body: `${path} changed after you opened it. Your unsaved edits are still in the editor.`,
    });
    if (choice === "reload") {
      try {
        const latest = await readLatest(path);
        showLatest(path, latest);
        toast(latest ? `reloaded latest ${path}` : `${path} was deleted elsewhere`, "ok", 3200);
      } catch (error) {
        announce(`Could not reload ${path}`);
        toast(`reload failed: ${error.message}`, "err", 7000);
      }
      return false;
    }
    if (choice !== "overwrite") return false;
    try {
      const latest = await readLatest(path);
      const result = await api.writeFile(path, content, latest ? latest.revision : null);
      return finishSave(path, content, result);
    } catch (error) {
      const detail = error.status === 409 ? "the file changed again; your edits are still here" : error.message;
      announce(`Could not overwrite ${path}`);
      toast(`overwrite failed: ${detail}`, "err", 7000);
      return false;
    }
  }

  async function save() {
    if (!current || busy) return false;
    if (current.exists && !isDirty()) return true;
    const path = current.path;
    const content = textarea.value;
    busy = true;
    refreshFlags();
    announce(`Saving ${path}`);
    try {
      const result = await api.writeFile(path, content, current.revision);
      return finishSave(path, content, result);
    } catch (error) {
      if (error.status === 409) return resolveSaveConflict(path, content);
      announce(`Could not save ${path}`);
      toast(`save failed: ${error.message}`, "err", 7000);
      return false;
    } finally {
      busy = false;
      refreshFlags();
    }
  }

  async function remove() {
    if (!current || !current.exists || busy) return;
    const path = current.path;
    const ok = await confirmDialog({
      title: "Delete file?",
      body: `${path} will be removed from the repository. This cannot be undone from the UI.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok || busy) return;
    busy = true;
    refreshFlags();
    announce(`Deleting ${path}`);
    try {
      await api.deleteFile(path, current.revision);
      await close({ force: true });
      announce(`Deleted ${path}`);
      toast(`deleted ${path}`, "ok", 2600);
      if (onDeleted) await onDeleted(path);
    } catch (error) {
      if (error.status === 409) {
        const choice = await conflictDialog({
          body: `${path} changed after you opened it. Review the latest version or explicitly delete it.`,
          keepLabel: "Keep file",
          overwriteLabel: "Delete latest",
        });
        if (choice === "reload") {
          try {
            const latest = await readLatest(path);
            if (latest) {
              showLatest(path, latest);
              toast(`reloaded latest ${path}`, "ok", 3200);
            } else {
              await close({ force: true });
              toast(`${path} was already deleted`, "ok", 3200);
              if (onDeleted) await onDeleted(path);
            }
          } catch (reloadError) {
            announce(`Could not reload ${path}`);
            toast(`reload failed: ${reloadError.message}`, "err", 7000);
          }
          return;
        }
        if (choice === "overwrite") {
          try {
            const latest = await readLatest(path);
            if (latest) await api.deleteFile(path, latest.revision);
            await close({ force: true });
            toast(`deleted ${path}`, "ok", 2600);
            if (onDeleted) await onDeleted(path);
          } catch (deleteError) {
            const detail = deleteError.status === 409 ? "the file changed again" : deleteError.message;
            announce(`Could not delete ${path}`);
            toast(`delete failed: ${detail}`, "err", 7000);
          }
        }
        return;
      }
      announce(`Could not delete ${path}`);
      toast(`delete failed: ${error.message}`, "err", 7000);
    } finally {
      busy = false;
      refreshFlags();
    }
  }

  showPane(false);
  refreshFlags();

  return {
    element,
    open,
    close,
    save,
    isDirty,
    path: () => (current ? current.path : null),
  };
}
