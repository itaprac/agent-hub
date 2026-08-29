// "Machines" panel: federation state from GET /api/peers plus remote sync/apply.
// Rendering mirrors status.js (pure render off the store); the actual hub run is
// injected by app.js so peer runs land in the same log drawer as local ones.

import { api } from "./api.js";
import { $, clear, el, formatTime } from "./dom.js";
import { confirmDialog } from "./modals.js";
import { store, update } from "./store.js";
import {
  createLocalController,
  initialPeersViewState,
  projectPeersView,
  reducePeersView,
} from "./view-state.js";

const AUTO_REFRESH_MS = 60000;
const PEERS_NAMES_KEY = "agent-hub:peer-names";

let runner = null; // (machine, command, dryRun) => Promise, provided by app.js
let timer = null;
const controller = createLocalController(initialPeersViewState, reducePeersView, () => renderPeers(store));

// ------------------------------------------------------------------ data

function rememberNames(machines) {
  const names = (machines || []).map((machine) => machine.machine).filter(Boolean);
  if (!names.length) return;
  try {
    localStorage.setItem(PEERS_NAMES_KEY, JSON.stringify(names));
  } catch (error) {
    /* ignore */
  }
}

function rememberedNames(state) {
  let names = [];
  try {
    const stored = JSON.parse(localStorage.getItem(PEERS_NAMES_KEY) || "[]");
    if (Array.isArray(stored)) names = stored.filter((name) => typeof name === "string" && name);
  } catch (error) {
    /* ignore */
  }
  if (state?.machine_id && !names.includes(state.machine_id)) names = [state.machine_id, ...names];
  return names;
}

export async function refreshPeers() {
  update({ peersLoading: true });
  try {
    const peers = await api.peers();
    peers.at = formatTime();
    rememberNames(peers.machines);
    update({ peers, peersSupported: true, peersError: null, peersLoading: false });
    // A backend that grew the endpoint since the last 404 gets its timer back.
    if (timer === null) startAutoRefresh();
  } catch (error) {
    if (error.status === 404) {
      // Backend without the peers endpoints: hide the panel, stay quiet.
      update({ peers: null, peersSupported: false, peersError: null, peersLoading: false });
      stopAutoRefresh();
      return;
    }
    update({ peersError: error.message, peersLoading: false });
  }
}

function stopAutoRefresh() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  timer = setInterval(() => {
    // Paused while a command is running (peer or local) to keep the fan-out quiet.
    if (store.busy > 0 || controller.state.running || !store.peersSupported) return;
    refreshPeers();
  }, AUTO_REFRESH_MS);
}

async function trigger(machine, command) {
  if (controller.state.running || !runner) return;
  const dryRun = controller.state.dryRun;

  if (command === "sync" && !dryRun) {
    const ok = await confirmDialog({
      title: `Run sync on ${machine}?`,
      body: "sync commits local changes, pulls with rebase, applies the state and pushes to the remote.",
      confirmLabel: "Run sync",
    });
    if (!ok) return;
  }

  controller.dispatch({ type: "command-started", machine, command });
  try {
    await runner(machine, command, dryRun);
  } finally {
    controller.dispatch({ type: "command-finished" });
    await refreshPeers();
  }
}

// No panel-local Refresh button: the top bar Refresh also calls refreshPeers().
// The 60 s auto-refresh below keeps the cards fresh in the meantime.
export function mountPeers({ run }) {
  runner = run;
  startAutoRefresh();
  $("#dry-run")?.addEventListener("change", (event) => {
    controller.dispatch({ type: "set-dry-run", value: event.target.checked });
  });
}

// ------------------------------------------------------------------ verdict

const count = (value) => (typeof value === "number" ? value : 0);

// Short, human reason for the "Diverged" badge, e.g. "macbook: 2 dirty, behind 1".
function driftReasons(machines) {
  const reasons = [];
  for (const machine of machines) {
    if (!machine.online || !machine.git) continue;
    const git = machine.git;
    const parts = [];
    if (count(git.dirty)) parts.push(`${git.dirty} dirty`);
    if (count(git.ahead)) parts.push(`ahead ${git.ahead}`);
    if (count(git.behind)) parts.push(`behind ${git.behind}`);
    if (parts.length) reasons.push(`${machine.machine}: ${parts.join(", ")}`);
  }

  const heads = new Set(
    machines.filter((machine) => machine.online && machine.git?.head?.sha).map((machine) => machine.git.head.sha)
  );
  if (heads.size > 1) reasons.push("different HEAD");
  return reasons;
}

function verdict(peers) {
  const machines = peers?.machines || [];
  if (!peers) return { tone: "idle", text: "Unknown", title: "no /api/peers answer yet" };

  if (peers.in_sync === true) {
    return {
      tone: "ok",
      text: "In sync",
      title: `${machines.length} machine${machines.length === 1 ? "" : "s"} online, same HEAD, nothing pending`,
    };
  }

  if (peers.in_sync === false) {
    const reasons = driftReasons(machines);
    const short = reasons.slice(0, 2).join(" · ");
    return {
      tone: "warn",
      text: short ? `Diverged · ${short}` : "Diverged",
      title: reasons.join("\n") || "machines are not in sync",
    };
  }

  const offline = machines.filter((machine) => !machine.online).map((machine) => machine.machine);
  return {
    tone: "idle",
    text: offline.length ? `Unknown · ${offline.join(", ")} offline` : "Unknown",
    title: offline.length ? `unreachable: ${offline.join(", ")}` : "sync state cannot be determined",
  };
}

// ------------------------------------------------------------------ cards

function commandButtons(machine, view) {
  const { dryRun, running } = view;
  const online = Boolean(machine.online);
  const commands = [
    { command: "sync", label: dryRun ? "Dry sync" : "Sync", className: "btn" },
    { command: "apply", label: dryRun ? "Dry apply" : "Apply", className: "btn btn-primary" },
  ];
  return commands.map(({ command, label, className }) => {
    const active = running && running.machine === machine.machine && running.command === command;
    return el(
      "button",
      {
        class: className,
        disabled: view.controlsDisabled || !online || machine.loading,
        title: machine.loading
          ? `waiting for ${machine.machine}`
          : online
            ? `hub.py ${command}${dryRun ? " --dry-run" : ""} on ${machine.machine}`
            : `${machine.machine} is unreachable`,
        onClick: () => trigger(machine.machine, command),
      },
      [active ? el("span", { class: "spin", "aria-hidden": "true" }) : null, label]
    );
  });
}

// "2 dirty", "behind 1", "3 problems" — the short reasons a machine is not clean.
function machineReasons(machine) {
  const git = machine.git || {};
  const reasons = [];
  if (count(git.dirty)) reasons.push(`${git.dirty} dirty`);
  if (count(git.ahead)) reasons.push(`ahead ${git.ahead}`);
  if (count(git.behind)) reasons.push(`behind ${git.behind}`);
  const problems = machine.status ? count(machine.status.problems) : 0;
  if (problems) reasons.push(`${problems} problem${problems === 1 ? "" : "s"}`);
  if (git.fetch_error) reasons.push("fetch failed");
  if (machine.status && machine.status.exit_code !== 0 && !problems) {
    reasons.push(`exit ${machine.status.exit_code}`);
  }
  return reasons;
}

function machineState(machine) {
  if (machine.loading) {
    return { tone: "idle", word: "loading", rest: "checking git and status" };
  }
  if (!machine.online) {
    return { tone: "bad", word: "unreachable", rest: machine.error || "no answer from this machine" };
  }
  if (!machine.git) return { tone: "warn", word: "unknown", rest: "no git information reported" };
  const reasons = machineReasons(machine);
  if (reasons.length) return { tone: "warn", word: "drift", rest: reasons.join(" · ") };
  return { tone: "ok", word: "in sync", rest: `clean · ${machine.git.remote || "no remote"}` };
}

function metaLine(key, value, toneClass) {
  return el("div", { class: "peer-line" }, [
    el("span", { class: "k", text: key }),
    el("span", { class: `v${toneClass ? ` ${toneClass}` : ""}`, text: value, title: value }),
  ]);
}

function card(machine, view) {
  const git = machine.git;
  const online = Boolean(machine.online);
  const state = machineState(machine);

  const classes = ["peer"];
  if (machine.local) classes.push("is-local");
  if (machine.loading) classes.push("is-loading");
  if (!online && !machine.loading) classes.push("is-offline");

  const head = el("div", { class: "peer-head" }, [
    el("span", { class: `peer-dot d-${state.tone}`, "aria-hidden": "true" }),
    el("span", { class: "peer-name", text: machine.machine, title: machine.url || machine.machine }),
    machine.local ? el("span", { class: "peer-tag", text: "this machine" }) : null,
  ]);

  const stateLine = el("div", { class: `peer-state s-${state.tone}` }, [
    el("em", { text: state.word }),
    state.rest ? el("span", { class: "x", text: ` · ${state.rest}` }) : null,
  ]);

  const lines = [];
  if (machine.loading) {
    lines.push(el("div", { class: "peer-skel" }));
    lines.push(el("div", { class: "peer-skel short" }));
    lines.push(el("div", { class: "peer-skel" }));
  } else if (online && git) {
    const head0 = git.head || {};
    const problems = machine.status ? count(machine.status.problems) : 0;
    const dirty = count(git.dirty);
    const ahead = count(git.ahead);
    const behind = count(git.behind);

    lines.push(metaLine("commit", `${head0.short || "—"}${git.branch ? ` · ${git.branch}` : ""}`));
    lines.push(metaLine("subject", head0.subject || "(no commit)"));
    lines.push(
      metaLine("tracking", `ahead ${ahead} · behind ${behind} · ${dirty} dirty`, ahead || behind || dirty ? "warn" : "")
    );
    lines.push(
      metaLine(
        "checks",
        machine.status ? `${problems} problem${problems === 1 ? "" : "s"} · exit ${machine.status.exit_code}` : "not reported",
        problems || (machine.status && machine.status.exit_code !== 0) ? "bad" : ""
      )
    );
    if (git.fetch_error) lines.push(metaLine("fetch", git.fetch_error, "bad"));
  } else {
    lines.push(metaLine("endpoint", machine.url || "local"));
    lines.push(metaLine("error", machine.error || "unreachable", "bad"));
  }

  return el("article", { class: classes.join(" ") }, [
    head,
    stateLine,
    el("div", { class: "peer-meta" }, lines),
    el("div", { class: "peer-actions" }, commandButtons(machine, view)),
  ]);
}

// ------------------------------------------------------------------ render

export function renderPeers(snapshot) {
  const panel = $("#peers");
  const view = projectPeersView(controller.state, {
    busy: snapshot.busy,
    loading: snapshot.peersLoading,
  });
  if (!snapshot.peersSupported) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("is-loading", Boolean(snapshot.peersLoading));

  const loading = Boolean(snapshot.peersLoading) && !snapshot.peers?.machines?.length;
  const { tone, text, title } = loading
    ? { tone: "idle", text: "Loading", title: "waiting for machine status" }
    : verdict(snapshot.peers);
  const pill = $("#peers-verdict");
  pill.className = `pill pill-${tone}`;
  pill.title = title;
  $("#peers-verdict-text").textContent = text;

  const live = snapshot.peers?.machines;
  const cached = rememberedNames(snapshot.state);
  const machines = live
    ? live
    : snapshot.peersLoading
      ? (cached.length ? cached : ["…", "…"]).map((name) => ({
          machine: name,
          loading: true,
          online: false,
        }))
      : [];
  const online = machines.filter((machine) => machine.online).length;
  $("#peers-meta").textContent = snapshot.peersLoading
    ? "loading…"
    : machines.length
      ? `${online}/${machines.length} online · ${snapshot.peers?.at || ""}`
      : "";
  const dryRun = $("#dry-run");
  dryRun.checked = view.dryRun;
  dryRun.disabled = view.controlsDisabled;

  const grid = clear($("#peers-grid"));
  if (snapshot.peersError) {
    grid.append(
      el("div", {
        class: "peer-error peer-error-block",
        role: "alert",
        text: `peers unavailable: ${snapshot.peersError}`,
      })
    );
  }
  if (!machines.length && !snapshot.peersError) {
    grid.append(el("div", { class: "tree-empty", text: "no machines reported" }));
    return;
  }
  for (const machine of machines) grid.append(card(machine, view));
}
