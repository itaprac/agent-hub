// Reusable file editor pane: load, dirty tracking, save (Cmd+S), revert, delete.

import { api } from "./api.js";
import { clear, el, formatBytes, splitPath, toast } from "./dom.js";
import { confirmDialog, conflictDialog } from "./modals.js";
import { createHighlightModel, languageFor } from "./highlight.js";

let editorSequence = 0;

export function createEditor({ onSaved, onDeleted, onDirty, emptyTitle = "Pick a file", emptyBody = "Select a file to edit it here." } = {}) {
  const editorId = ++editorSequence;
  let current = null; // {path, exists, revision}
  let baseline = "";
  let busy = false;

  const pathLabel = el("span", { class: "editor-path" });
  const flag = el("span", { class: "editor-flag" }, [el("span", { class: "dot", "aria-hidden": "true" }), el("span", { class: "flag-text", text: "Saved" })]);
  const flagText = flag.lastChild;
  const revertButton = el("button", { type: "button", class: "btn ghost sm", text: "Revert", onClick: revert, disabled: true });
  const deleteButton = el("button", { type: "button", class: "btn ghost sm danger", text: "Delete file", onClick: remove, disabled: true });
  const saveButton = el("button", { type: "button", class: "btn primary sm", text: "Save", onClick: save, disabled: true, "aria-keyshortcuts": "Meta+S" });

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
  textarea.setAttribute("rows", "1");

  // The highlighted copy sits under a transparent-text textarea. Both share one
  // grid cell, font and padding, so wrapping and line positions match; the
  // textarea stays the real input for focus, undo, selection and screen readers.
  const model = createHighlightModel();
  const overlay = el("pre", { class: "code-hl", "aria-hidden": "true" });
  const code = el("div", { class: "code" }, [overlay, textarea]);
  let currentLine = -1;

  const language = el("span", { class: "editor-lang", text: "" });
  const position = el("span", { text: "Ln 1, Col 1" });
  const size = el("span", { text: "" });
  const editorStatus = el("span", {
    class: "sr-only",
    role: "status",
    "aria-live": "polite",
    "aria-atomic": "true",
  });

  const head = el("div", { class: "editor-head" }, [
    el("div", { class: "editor-id" }, [pathLabel, flag]),
    el("div", { class: "editor-acts" }, [revertButton, deleteButton, saveButton]),
    editorStatus,
  ]);
  const body = el("div", { class: "editor-body" }, [textareaLabel, code]);
  const foot = el("div", { class: "editor-foot" }, [
    language, position, el("span", { class: "spacer" }), size,
    el("span", { class: "editor-hint" }, [el("kbd", { text: "⌘S" }), " save"]),
  ]);
  const placeholder = el("div", { class: "editor-placeholder" }, [
    el("strong", { text: emptyTitle }),
    el("span", { text: emptyBody }),
  ]);

  const element = el("div", { class: "editor" }, [head, placeholder]);

  textarea.addEventListener("input", () => {
    paintEdit();
    refreshFlags();
  });
  textarea.addEventListener("keyup", refreshPosition);
  textarea.addEventListener("click", refreshPosition);
  textarea.addEventListener("select", refreshPosition);
  // Height follows the content, so the textarea itself must never scroll.
  textarea.addEventListener("scroll", () => {
    textarea.scrollTop = 0;
    textarea.scrollLeft = 0;
  });
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      // execCommand keeps the native undo stack; setRangeText is the fallback.
      const inserted = typeof document.execCommand === "function" && document.execCommand("insertText", false, "  ");
      if (!inserted) {
        textarea.setRangeText("  ", textarea.selectionStart, textarea.selectionEnd, "end");
        paintEdit();
        refreshFlags();
      }
    }
  });

  function rowsHtml(rows) {
    let html = "";
    for (const row of rows) html += `<div class="ln${row.cls ? ` ln-${row.cls}` : ""}">${row.html}</div>`;
    return html;
  }

  function setDigits() {
    code.style.setProperty("--digits", String(Math.max(2, String(model.count()).length)));
  }

  // Full highlight, used when a file opens or its text is replaced.
  function paintAll() {
    model.setLanguage(languageFor(current?.path));
    language.textContent = current ? model.label() : "";
    overlay.innerHTML = rowsHtml(model.reset(textarea.value));
    currentLine = -1;
    setDigits();
  }

  // Patch only the lines an edit changed.
  function paintEdit() {
    const patch = model.update(textarea.value);
    const children = overlay.children;
    for (let index = 0; index < patch.removed; index += 1) children[patch.start].remove();
    if (patch.rows.length) {
      const holder = document.createElement("div");
      holder.innerHTML = rowsHtml(patch.rows);
      const fragment = document.createDocumentFragment();
      while (holder.firstChild) fragment.append(holder.firstChild);
      overlay.insertBefore(fragment, children[patch.start] || null);
    }
    currentLine = -1;
    setDigits();
  }

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
    const caret = textarea.selectionStart;
    const value = textarea.value;
    let line = 0;
    let lineStart = 0;
    for (let index = value.indexOf("\n"); index >= 0 && index < caret; index = value.indexOf("\n", index + 1)) {
      line += 1;
      lineStart = index + 1;
    }
    position.textContent = `Ln ${line + 1}, Col ${caret - lineStart + 1}`;
    if (line !== currentLine) {
      overlay.children[currentLine]?.classList.remove("cur");
      overlay.children[line]?.classList.add("cur");
      currentLine = line;
    }
  }

  function refreshFlags() {
    const dirty = isDirty();
    saveButton.disabled = busy || !current || (!dirty && current.exists);
    saveButton.textContent = current && !current.exists ? "Create" : "Save";
    revertButton.disabled = busy || !dirty;
    deleteButton.disabled = busy || !current || !current.exists;
    const isNew = Boolean(current && !current.exists);
    flag.hidden = !current;
    flag.className = `editor-flag${isNew ? " new" : dirty ? " dirty" : ""}`;
    flagText.textContent = isNew ? (dirty ? "New file · unsaved" : "New file") : dirty ? "Unsaved changes" : "Saved";
    size.textContent = current ? formatBytes(new TextEncoder().encode(textarea.value).length) : "";
    refreshPosition();
    if (onDirty) onDirty(isDirty());
  }

  function showPane(hasFile) {
    clear(element);
    element.classList.toggle("is-empty", !hasFile);
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
    paintAll();
    refreshFlags();
    textarea.focus({ preventScroll: true });
    // Assigning .value parks the caret at the end of the text; put it back at the
    // top so the pane opens on line 1 and the footer agrees with the caret.
    textarea.setSelectionRange(0, 0);
    body.scrollTop = 0;
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
    paintAll();
    refreshFlags();
    return true;
  }

  function revert() {
    if (!current) return;
    textarea.value = baseline;
    paintAll();
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
    paintAll();
    refreshFlags();
    textarea.focus({ preventScroll: true });
    textarea.setSelectionRange(0, 0);
    body.scrollTop = 0;
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

  async function reload() {
    if (!current) return false;
    const path = current.path;
    try {
      showLatest(path, await readLatest(path));
      return true;
    } catch (error) {
      toast(`reload failed: ${error.message}`, "err", 7000);
      return false;
    }
  }

  showPane(false);
  refreshFlags();

  return {
    element,
    open,
    close,
    save,
    reload,
    confirmDiscard,
    isDirty,
    path: () => (current ? current.path : null),
    revision: () => (current ? current.revision : null),
    text: () => textarea.value,
    focus: () => textarea.focus({ preventScroll: true }),
  };
}
