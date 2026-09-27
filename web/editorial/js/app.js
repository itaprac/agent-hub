// Bootstrap: top bar actions, tab routing, and store -> DOM rendering.

import { api } from "./api.js";
import { $, $$, formatTime, refreshAges, toast } from "./dom.js";
import { adoptProjectField, formDialog, projectField } from "./modals.js";
import { mountFleet, refreshFleet, renderFleet } from "./fleet.js";
import { renderLog, renderStatusView, summarize } from "./status.js";
import { store, subscribe, update, withBusy } from "./store.js";
import { mountSettings, renderSettings } from "./settings.js";
import { mountTheme } from "./theme.js";
import { mountLayoutSwitch } from "./layout.js";
import { createLifecycle } from "./lifecycle.js";
import { editAgents, showSkillTargets } from "./targets.js";
import { isUsageLoading, mountUsage, refreshUsage, renderUsage } from "./usage.js";
import { buildConfigTree, buildInstructionsTree, buildSkillsTree, createWorkspace } from "./workspace.js";
import { recordAge } from "./fleet.js";

const LOG_KEY = "agent-hub:log-open";
const TABS = ["status", "usage", "skills", "instructions", "config"];
const ALL_TABS = [...TABS, "settings"];

let renderedState = null;
let renderedStatus = null;

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
    result.checked_at = new Date().toISOString();
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

async function refreshEverything({ log = false } = {}) {
  announce("Refreshing Store and Fleet");
  try {
    await refreshAll({ log });
    await refreshFleet();
    if (store.tab === "usage") await refreshUsage();
  } finally {
    announce("Store and Fleet refreshed");
  }
}

async function runHub(label, invoke) {
  return withBusy(async () => {
    let result;
    try {
      result = await invoke();
    } catch (error) {
      toast(`${label} failed: ${error.message}`, "err", 8000);
      const failed = { command: label, exit_code: 1, lines: [{ level: "ERROR", text: error.message }], at: formatTime() };
      update({ log: failed });
      setLogOpen(true);
      return failed;
    }
    result.at = formatTime();
    update({ log: result });
    if (result.command === "sync-all") {
      toast(result.exit_code === 0 ? "All machines synced" : "Sync incomplete. Check the waiting Machines.",
        result.exit_code === 0 ? "ok" : "err", 5000);
    } else if (result.exit_code === 0) {
      toast(`${label} finished`, "ok", 2400);
    } else {
      const first = (result.lines || []).find((line) => ["ERROR", "DRIFT", "MISSING", "STALE", "CONFLICT"].includes(line.level));
      toast(`${label} exited ${result.exit_code}${first ? `: ${first.text}` : ""}`, "err", 9000);
      setLogOpen(true);
    }
    await refreshAll();
    await reloadConfigEditor();
    return result;
  });
}

function runFleetCommand(command, dryRun, machine) {
  const target = machine || store.state?.machine_id || "this machine";
  const label = command === "sync-all" ? "Sync all machines" : `${command}${dryRun ? " --dry-run" : ""} on ${target}`;
  return runHub(label, async () => {
    const result = await api.run(command, dryRun, machine);
    return { ...result, display_command: label };
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
  await refreshFleet();
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
  const lifecycle = createLifecycle({ runHub, refresh: afterEdit });
  workspaces.skills = createWorkspace($("#view-skills"), {
    title: "Skills",
    layout: "cards",
    itemIcon: "spark",
    lede: skillsLede,
    buildTree: buildSkillsTree,
    buildContext: lifecycle.skillContext,
    onChanged: afterEdit,
    onDirty: paintDirty,
    actions: [
      { label: "Adopt", title: "agent-hub adopt: move an existing skill directory into the Store", kind: "ghost", run: adoptSkill },
      { label: "Update", title: "Update installed skills", kind: "ghost", run: updateSkills },
      { label: "Install", title: "Install a skill from a source", icon: "down", run: installSkill },
      { label: "New skill", title: "agent-hub add-skill", kind: "primary", icon: "plus", run: newSkill },
    ],
  });
  workspaces.instructions = createWorkspace($("#view-instructions"), {
    title: "Instructions",
    lede: () => "One shared file, with per-agent overlays appended on each machine.",
    buildTree: buildInstructionsTree,
    buildContext: lifecycle.instructionContext,
    onChanged: afterEdit,
    onDirty: paintDirty,
  });
  workspaces.config = createWorkspace($("#view-config"), {
    title: "Config",
    itemIcon: "config",
    lede: (state) => `Store settings shared by every machine · ${state.hub?.enabled ? `${state.hub.enabled.length} Agents` : "detected Agents"} · ${state.hub?.mode || "symlink"} mode`,
    buildTree: buildConfigTree,
    buildContext: configContext,
    onChanged: afterEdit,
    onDirty: paintDirty,
  });
}

function skillsLede(state) {
  const count = (state.skills?.global?.length || 0)
    + Object.values(state.skills?.projects || {}).reduce((sum, list) => sum + list.length, 0);
  const machines = (state.machines || []).map((machine) => machine.machine);
  const where = machines.length > 1 ? `across ${machines.slice(0, -1).join(", ")} and ${machines.at(-1)}` : machines.length ? `on ${machines[0]}` : "in this Store";
  return `${count} skill${count === 1 ? "" : "s"} shared ${where}`;
}

function configContext(path, state) {
  if (path !== "hub.toml") return null;
  return {
    label: "hub.toml",
    note: `${state.hub?.enabled ? `${state.hub.enabled.length} Agents` : "detected Agents"} · ${state.hub?.mode || "symlink"} · ${Object.keys(state.hub?.skills || {}).length} Skill targets`,
    actions: [
      { label: "Agents & mode", title: "Choose the Agents that Apply targets", run: () => editAgents(state, runHub) },
      { label: "Skill targets", title: "Choose the Machines and Agents for each Skill", run: () => showSkillTargets(state, runHub) },
    ],
  };
}

// Show a settings change in an open, unchanged hub.toml.
async function reloadConfigEditor() {
  const editor = workspaces.config?.editor;
  if (editor && editor.path() === "hub.toml" && !editor.isDirty()) await editor.reload();
}

async function installSkill() {
  const values = await formDialog({
    title: "Install a skill",
    sub: "Installs skills into this Store and records their source.",
    confirmLabel: "Install",
    fields: [
      { name: "source", label: "Source", required: true, placeholder: "owner/repository or a URL" },
      { name: "skill", label: "Skill (optional)", placeholder: "all skills from this source" },
    ],
  });
  if (values) await runHub("install", () => api.install(values.source, values.skill));
}

async function updateSkills() {
  await runHub("update", () => api.update());
}

async function newSkill() {
  const projects = store.state?.projects || [];
  const values = await formDialog({
    title: "New skill",
    sub: "Runs agent-hub add-skill and writes a SKILL.md template.",
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
  const values = await formDialog({
    title: "Adopt an existing skill",
    sub: "Moves a directory into the Store and leaves a symlink behind.",
    confirmLabel: "Adopt",
    fields: [
      { name: "path", label: "Directory path", required: true, placeholder: "~/.claude/skills/my-skill" },
      adoptProjectField(),
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
  for (const [name, workspace] of Object.entries(workspaces)) {
    if (name === tab) workspace.activate();
    else workspace.deactivate();
  }
  for (const view of $$(".view")) view.hidden = view.dataset.view !== tab;
  document.title = `${tab[0].toUpperCase()}${tab.slice(1)} · agent-hub`;
  if (changed) window.scrollTo({ top: 0 });
  if (location.hash.slice(1) !== tab) history.replaceState(null, "", `#${tab}`);
  if (tab === "usage" && (changed || !store.usage) && !isUsageLoading()) refreshUsage();
}

function setLogOpen(open) {
  $("#logbar").classList.toggle("open", open);
  $("#logbar").hidden = !open;
  $("#log-toggle").setAttribute("aria-expanded", String(open));
  if (open) {
    const body = $("#log-body");
    body.scrollTop = body.scrollHeight;
  }
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
  $("#machine-chip").title = state ? `Store: ${state.repo}` : "";
  $("#log-toggle").classList.toggle("has-error", Boolean(snapshot.log && snapshot.log.exit_code !== 0));

  const banner = $("#banner");
  const messages = snapshot.stateError ? [`configuration error: ${snapshot.stateError}`] : state?.warnings || [];
  banner.hidden = !messages.length;
  banner.textContent = messages.join(" · ");

  const busy = snapshot.busy > 0;
  $("#progress").hidden = !busy;
  $("#btn-refresh").disabled = busy;
}

function render(snapshot) {
  renderChrome(snapshot);
  renderFleet(snapshot);
  renderStatusView(snapshot);
  renderUsage(snapshot);
  renderSettings(snapshot);
  renderLog(snapshot.log);
  if (snapshot.state !== renderedState || snapshot.status !== renderedStatus) {
    renderedState = snapshot.state;
    renderedStatus = snapshot.status;
    for (const workspace of Object.values(workspaces)) workspace.render(snapshot.state, snapshot.status);
  }
  paintDirty();
}

// ------------------------------------------------------------------ boot

function wire() {
  // Refresh local Store state and its Machine records.
  $("#btn-refresh").addEventListener("click", () => withBusy(() => refreshEverything({ log: true })));

  const tabs = $$(".tab");
  const tablist = $(".nav");
  const mobileTabs = window.matchMedia("(max-width: 720px)");
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

  $("#log-toggle").addEventListener("click", () => setLogOpen($("#logbar").hidden));
  $("#log-close").addEventListener("click", () => {
    setLogOpen(false);
    $("#log-toggle").focus();
  });

  // The active state is painted by renderStatusView off the store.
  for (const button of $$("#status-filter button")) {
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
      if (document.querySelector("dialog[open]")) return;
      const workspace = workspaces[store.tab];
      if (workspace?.isSheetOpen()) {
        event.preventDefault();
        workspace.closeSheet();
        return;
      }
      if (!$("#logbar").hidden && !(event.target.closest && event.target.closest(".theme-picker"))) {
        event.preventDefault();
        setLogOpen(false);
        $("#log-toggle").focus();
        return;
      }
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
    if (workspaces[store.tab]?.isSheetOpen() || document.querySelector("dialog[open]")) return;
    const index = Number(event.key);
    if (index >= 1 && index <= TABS.length) setTab(TABS[index - 1]);
    else if (event.key.toLowerCase() === "r") withBusy(() => refreshEverything({ log: true }));
    else if (event.key.toLowerCase() === "l") setLogOpen($("#logbar").hidden);
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
  mountLayoutSwitch("editorial");

  let logOpen = false;
  try {
    logOpen = localStorage.getItem(LOG_KEY) === "1";
  } catch (error) {
    /* ignore */
  }

  setupWorkspaces();
  wire();
  mountFleet({ run: runFleetCommand });
  mountUsage();
  mountSettings();
  subscribe(render);
  setLogOpen(logOpen);
  setTab(location.hash.slice(1) || "status");
  render(store);
  // Relative times ("checked 2m ago") stay current without a full re-render.
  setInterval(() => refreshAges(recordAge), 30000);

  await withBusy(() => refreshEverything({ log: true }));
}

boot();
