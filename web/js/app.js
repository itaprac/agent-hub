// Bootstrap: top bar actions, tab routing, and store -> DOM rendering.

import { api } from "./api.js";
import { $, $$, formatTime, toast } from "./dom.js";
import { formDialog, projectField } from "./modals.js";
import { mountPeers, refreshPeers, renderPeers } from "./peers.js";
import { renderLog, renderStatusView, summarize } from "./status.js";
import { store, subscribe, update, withBusy } from "./store.js";
import { mountSettings, renderSettings } from "./settings.js";
import { mountTheme } from "./theme.js";
import { isUsageLoading, mountUsage, refreshUsage, renderUsage } from "./usage.js";
import { buildConfigTree, buildInstructionsTree, buildSkillsTree, createWorkspace } from "./workspace.js";

const LOG_KEY = "agent-hub:log-open";
const TABS = ["status", "usage", "skills", "instructions", "config"];
const ALL_TABS = [...TABS, "settings"];

let renderedState = null;

function announce(message) {
  const live = $("#app-status");
  if (!live) return;
  live.textContent = "";
  requestAnimationFrame(() => {
    live.textContent = message;
  });
}

// ------------------------------------------------------------------ data

async function refreshState() {
  try {
    const state = await api.state();
    update({ state, stateError: null });
  } catch (error) {
    update({ stateError: error.message });
  }
}

async function refreshStatus({ log = false } = {}) {
  try {
    const result = await api.status();
    result.at = formatTime();
    // The Problems filter is hidden when nothing is wrong, so drop it as well —
    // otherwise the list would stay filtered with no visible way back.
    const filter = summarize(result).problems ? store.filter : "all";
    update({ status: result, log: log ? result : store.log || result, filter });
  } catch (error) {
    toast(`status failed: ${error.message}`, "err", 7000);
  }
}

async function refreshAll({ log = false } = {}) {
  await refreshState();
  await refreshStatus({ log });
}

// The peers fan-out talks to other machines, so it only runs on explicit
// refreshes, the 60 s timer and after a peer command — not after every save.
async function refreshEverything({ log = false } = {}) {
  announce("Refreshing repository and machine state");
  try {
    // /api/status and /api/peers both inspect the local repository. Keep them
    // sequential now that the server rejects lock contention instead of waiting.
    const tasks = [refreshState(), refreshStatus({ log })];
    if (store.tab === "usage") tasks.push(refreshUsage());
    await Promise.all(tasks);
    await refreshPeers();
  } finally {
    announce("Repository and machine state refreshed");
  }
}

async function runHub(label, invoke) {
  return withBusy(async () => {
    let result;
    try {
      result = await invoke();
    } catch (error) {
      toast(`${label} failed: ${error.message}`, "err", 8000);
      return null;
    }
    result.at = formatTime();
    update({ log: result });
    if (result.exit_code === 0) {
      toast(`${label} finished`, "ok", 2400);
    } else {
      const first = (result.lines || []).find((line) => ["ERROR", "DRIFT", "MISSING", "STALE"].includes(line.level));
      toast(`${label} exited ${result.exit_code}${first ? `: ${first.text}` : ""}`, "err", 9000);
      setLogOpen(true);
    }
    await refreshAll();
    return result;
  });
}

// A peer run reuses runHub, so the result lands in the same log drawer, raises
// the same toasts and triggers the same follow-up refresh as a local command.
function runPeer(machine, command, dryRun) {
  const suffix = dryRun ? " --dry-run" : "";
  return runHub(`${machine}: ${command}${suffix}`, async () => {
    const result = await api.peerRun(machine, command, dryRun);
    result.machine = machine;
    result.command = result.command || `${command}${suffix}`;
    return result;
  });
}

// ------------------------------------------------------------------ workspaces

const workspaces = {};

function currentEditor() {
  const workspace = workspaces[store.tab];
  return workspace ? workspace.editor : null;
}

async function afterEdit() {
  await refreshAll();
}

function paintDirty() {
  for (const tab of ["skills", "instructions", "config"]) {
    const button = $(`.tab[data-tab="${tab}"]`);
    const dot = button?.querySelector(".tab-dot");
    const dirty = Boolean(workspaces[tab]?.editor.isDirty());
    if (dot) dot.hidden = !dirty;
    if (button) {
      if (dirty) button.setAttribute("aria-label", `${tab[0].toUpperCase()}${tab.slice(1)}, unsaved edits`);
      else button.removeAttribute("aria-label");
    }
  }
}

function setupWorkspaces() {
  workspaces.skills = createWorkspace($("#view-skills"), {
    title: "Skills",
    buildTree: buildSkillsTree,
    onChanged: afterEdit,
    onDirty: paintDirty,
    actions: [
      { label: "New", title: "hub.py add-skill", run: newSkill },
      { label: "Adopt", title: "hub.py adopt", run: adoptSkill },
    ],
  });
  workspaces.instructions = createWorkspace($("#view-instructions"), {
    title: "Instructions",
    buildTree: buildInstructionsTree,
    onChanged: afterEdit,
    onDirty: paintDirty,
  });
  workspaces.config = createWorkspace($("#view-config"), {
    title: "Config",
    buildTree: buildConfigTree,
    onChanged: afterEdit,
    onDirty: paintDirty,
  });
}

async function newSkill() {
  const projects = store.state?.projects || [];
  const values = await formDialog({
    title: "New skill",
    sub: "Runs hub.py add-skill and writes a SKILL.md template.",
    confirmLabel: "Create skill",
    fields: [
      { name: "name", label: "Skill name", required: true, placeholder: "code-review" },
      projectField(projects, { hint: "Global skills go to every agent that supports skills." }),
    ],
  });
  if (!values) return;
  await runHub("add-skill", () => api.addSkill(values.name, values.project));
}

async function adoptSkill() {
  const projects = store.state?.projects || [];
  const values = await formDialog({
    title: "Adopt an existing skill",
    sub: "Moves a directory into the repository and leaves a symlink behind.",
    confirmLabel: "Adopt",
    fields: [
      { name: "path", label: "Directory path", required: true, placeholder: "~/.claude/skills/my-skill" },
      projectField(projects),
      { name: "name", label: "Skill name (optional)", placeholder: "defaults to the directory name" },
    ],
  });
  if (!values) return;
  await runHub("adopt", () => api.adopt(values.path, values.project, values.name));
}

// ------------------------------------------------------------------ chrome

function setTab(tab) {
  if (!ALL_TABS.includes(tab)) tab = "status";
  const changed = store.tab !== tab;
  update({ tab });
  for (const button of $$(".tab")) {
    const on = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(on));
    button.tabIndex = on ? 0 : -1;
    if (on) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  const railSettings = $("#btn-rail-settings");
  if (railSettings) {
    railSettings.classList.toggle("is-active", tab === "settings");
    if (tab === "settings") railSettings.setAttribute("aria-current", "page");
    else railSettings.removeAttribute("aria-current");
  }
  for (const view of $$(".view")) view.hidden = view.dataset.view !== tab;
  $("#crumb-view").textContent = tab;
  if (location.hash.slice(1) !== tab) history.replaceState(null, "", `#${tab}`);
  if (tab === "usage" && (changed || !store.usage) && !isUsageLoading()) refreshUsage();
}

function setLogOpen(open) {
  $("#logbar").classList.toggle("open", open);
  $("#log-body").hidden = !open;
  $("#log-toggle").setAttribute("aria-expanded", String(open));
  try {
    localStorage.setItem(LOG_KEY, open ? "1" : "0");
  } catch (error) {
    /* private mode: ignore */
  }
}

function renderChrome(snapshot) {
  const state = snapshot.state;
  $("#machine-id").textContent = state ? state.machine_id : "—";
  $("#machine-host").textContent = state ? state.hostname : "—";
  $("#repo-path").textContent = state ? state.repo : "";
  $("#repo-path").title = state ? state.repo : "";

  const banner = $("#banner");
  banner.hidden = !snapshot.stateError;
  banner.textContent = snapshot.stateError ? `configuration error: ${snapshot.stateError}` : "";

  const busy = snapshot.busy > 0;
  $("#progress").hidden = !busy;
  $("#btn-refresh").disabled = busy;
}

function render(snapshot) {
  renderChrome(snapshot);
  renderPeers(snapshot);
  renderStatusView(snapshot);
  renderUsage(snapshot);
  renderSettings(snapshot);
  renderLog(snapshot.log);
  if (snapshot.state !== renderedState) {
    renderedState = snapshot.state;
    for (const workspace of Object.values(workspaces)) workspace.render(snapshot.state);
  }
  paintDirty();
}

// ------------------------------------------------------------------ boot

function wire() {
  // The one global refresh: state + status + peers. Apply/Sync live on the
  // machine cards (the local machine always has one, see SPEC-PEERS section 2).
  $("#btn-refresh").addEventListener("click", () => withBusy(() => refreshEverything({ log: true })));

  const tabs = $$(".tab");
  const tablist = $(".rail-nav");
  const mobileTabs = window.matchMedia("(max-width: 600px)");
  const syncTabOrientation = () => {
    tablist.setAttribute("aria-orientation", mobileTabs.matches ? "horizontal" : "vertical");
  };
  syncTabOrientation();
  mobileTabs.addEventListener("change", syncTabOrientation);
  for (const button of tabs) button.addEventListener("click", () => setTab(button.dataset.tab));
  $("#btn-rail-settings").addEventListener("click", () => setTab("settings"));
  tablist.addEventListener("keydown", (event) => {
    const current = event.target.closest('[role="tab"]');
    if (!current) return;
    const index = tabs.indexOf(current);
    const horizontal = tablist.getAttribute("aria-orientation") === "horizontal";
    const forwardKey = horizontal ? "ArrowRight" : "ArrowDown";
    const backwardKey = horizontal ? "ArrowLeft" : "ArrowUp";
    let next = null;
    if (event.key === forwardKey) next = (index + 1) % tabs.length;
    else if (event.key === backwardKey) next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    setTab(tabs[next].dataset.tab);
    tabs[next].focus();
  });

  $("#log-toggle").addEventListener("click", () => setLogOpen($("#log-body").hidden));

  // The active class is painted by renderStatusView off the store.
  for (const button of $$("#status-filter .seg")) {
    button.addEventListener("click", () => update({ filter: button.dataset.filter }));
  }

  window.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      const editor = currentEditor();
      if (editor) editor.save();
      return;
    }
    if (event.key === "Escape") {
      const search = document.querySelector(".view:not([hidden]) .search");
      if (search && document.activeElement === search) {
        if (search.value) {
          search.value = "";
          search.dispatchEvent(new Event("input", { bubbles: true }));
        } else {
          search.blur();
        }
        event.preventDefault();
      }
      return;
    }
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
    const index = Number(event.key);
    if (index >= 1 && index <= TABS.length) setTab(TABS[index - 1]);
    else if (event.key.toLowerCase() === "r") withBusy(() => refreshEverything({ log: true }));
    else if (event.key.toLowerCase() === "l") setLogOpen($("#log-body").hidden);
    else if (event.key === "/") {
      const search = document.querySelector(".view:not([hidden]) .search");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (Object.values(workspaces).some((workspace) => workspace.editor.isDirty())) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  window.addEventListener("hashchange", () => setTab(location.hash.slice(1)));
}

async function boot() {
  mountTheme();

  let logOpen = false;
  try {
    logOpen = localStorage.getItem(LOG_KEY) === "1";
  } catch (error) {
    /* ignore */
  }

  setupWorkspaces();
  wire();
  mountPeers({ run: runPeer });
  mountUsage();
  mountSettings();
  subscribe(render);
  setLogOpen(logOpen);
  setTab(location.hash.slice(1) || "status");
  render(store);

  await withBusy(() => refreshEverything({ log: true }));
}

boot();
