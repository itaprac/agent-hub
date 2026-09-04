// Fleet reads Store records. Configured machines can receive explicit commands.
import { api } from "./api.js";
import { $, clear, el, formatTime } from "./dom.js";
import { store, update } from "./store.js";

export function createFleetController({ request, publish = () => {}, render = () => {},
  now = () => "", isBusy = () => false, canRun = (machine) => machine === null,
  schedule = setInterval, cancel = clearInterval }) {
  let state = { dryRuns: new Map(), errors: new Map(), running: null };
  let runner = null;
  let timer = null;
  let pending = null;
  const view = ({ busy = 0, loading = false, machine = null } = {}) => Object.freeze({
    dryRun: state.dryRuns.get(machine) || false,
    error: state.errors.get(machine) || null,
    running: state.running ? Object.freeze({ ...state.running }) : null,
    controlsDisabled: Number(busy) > 0 || Boolean(state.running) || Boolean(loading),
  });
  const change = (patch) => { state = { ...state, ...patch }; render(view()); };
  function refresh() {
    if (pending) return pending;
    publish({ fleetLoading: true });
    pending = Promise.resolve().then(request).then((result) => {
      publish({ fleet: { ...result, at: now() }, fleetError: null, fleetLoading: false });
      return true;
    }, (error) => {
      publish({ fleetError: error.message, fleetLoading: false });
      return false;
    }).finally(() => { pending = null; });
    return pending;
  }
  function stopAutoRefresh() {
    if (timer !== null) cancel(timer);
    timer = null;
  }
  return {
    view, refresh, stopAutoRefresh,
    startAutoRefresh() {
      stopAutoRefresh();
      timer = schedule(() => isBusy() || state.running ? false : refresh(), 60000);
    },
    setRunner(next) { runner = next; },
    setDryRun(value, machine = null) {
      if (state.running) return false;
      change({ dryRuns: new Map(state.dryRuns).set(machine, Boolean(value)) });
      return true;
    },
    async run(command, machine = null) {
      if (state.running || isBusy() || !runner || !canRun(machine) || !["apply", "sync"].includes(command)) return false;
      const dryRun = state.dryRuns.get(machine) || false;
      change({ running: { command, dryRun, machine }, errors: new Map(state.errors).set(machine, null) });
      try {
        const result = await runner(command, dryRun, machine);
        if (result && result.exit_code !== 0) {
          const problem = result.lines?.find((line) => ["ERROR", "CONFLICT", "DRIFT", "MISSING"].includes(line.level));
          change({ errors: new Map(state.errors).set(machine, problem?.text || `${command} failed; see the log.`) });
        }
      } catch (error) {
        change({ errors: new Map(state.errors).set(machine, error.message) });
      } finally {
        try { await refresh(); } finally { change({ running: null }); }
      }
      return true;
    },
  };
}

const controller = createFleetController({
  request: () => api.fleet(), publish: update, render: () => renderFleet(store),
  now: formatTime, isBusy: () => store.busy > 0,
  canRun: (machine) => machine === null || (store.fleet?.machines || []).some(
    (record) => record.machine === machine && record.remote_control === true && !record.local
  ),
});
export const refreshFleet = () => controller.refresh();
export function mountFleet({ run }) {
  controller.setRunner(run);
  controller.startAutoRefresh();
}

export function recordAge(seconds) {
  if (!Number.isFinite(seconds)) return "not recorded";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function machineState(machine) {
  if (machine.error) return { tone: "bad", word: "record error", rest: machine.error };
  const problems = Number.isFinite(machine.problems) ? machine.problems : null;
  const word = machine.current ? "current" : Number.isFinite(machine.behind) ? `behind ${machine.behind}` : "unknown";
  return {
    tone: problems > 0 ? "bad" : machine.current ? "ok" : "warn",
    word,
    rest: problems === null ? "checks not recorded" : `${problems} problem${problems === 1 ? "" : "s"}`,
  };
}

function metaLine(key, value) {
  return el("div", { class: "fleet-line" }, [
    el("span", { class: "k", text: key }), el("span", { class: "v", text: value, title: value }),
  ]);
}
function card(machine, view) {
  const state = machineState(machine);
  const target = machine.local ? null : machine.machine;
  const controllable = machine.local || machine.remote_control === true;
  const active = view.running?.machine === target;
  const checkboxId = machine.local ? "dry-run" : `dry-run-${machine.machine}`;
  const commands = controllable ? ["sync", "apply"].map((command) => el("button", {
    class: `btn${command === "apply" ? " btn-primary" : ""}`,
    disabled: view.controlsDisabled,
    title: !machine.local && command === "sync" && !view.dryRun
      ? `Publish this Store, sync on ${machine.machine}, then refresh its record`
      : `${command[0].toUpperCase()}${command.slice(1)} on ${machine.machine}${view.dryRun ? " (dry-run)" : ""}`,
    onClick: () => controller.run(command, target),
  }, [active && view.running?.command === command ? el("span", { class: "spin", "aria-hidden": "true" }) : null,
    `${view.dryRun ? "Dry " : ""}${command[0].toUpperCase()}${command.slice(1)}`])) : [];
  return el("article", { class: `fleet${machine.local ? " is-local" : ""}` }, [
    el("div", { class: "fleet-head" }, [
      el("span", { class: `fleet-dot d-${state.tone}`, "aria-hidden": "true" }),
      el("span", { class: "fleet-name", text: machine.machine }),
      machine.local ? el("span", { class: "fleet-tag", text: "this machine" }) : null,
    ]),
    el("div", { class: `fleet-state s-${state.tone}` }, [el("em", { text: state.word }),
      el("span", { class: "x", text: ` · ${state.rest}` })]),
    el("div", { class: "fleet-meta" }, [
      metaLine("commit", typeof machine.head === "string" ? machine.head.slice(0, 12) : "not recorded"),
      metaLine("last sync", recordAge(machine.age_seconds)),
      metaLine("recorded", machine.synced_at || "not recorded"),
    ]),
    view.error ? el("div", { class: "fleet-error", role: "alert", text: `Last command failed: ${view.error}` }) : null,
    controllable ? el("div", { class: "fleet-controls" }, [
      active ? el("span", { class: "sec-note", role: "status", text: `${view.running.command === "sync" ? "Sync" : "Apply"} on ${machine.machine}…` }) : null,
      el("label", { class: "switch", title: `Run Apply and Sync with --dry-run on ${machine.machine}` }, [
        el("input", { type: "checkbox", id: checkboxId, checked: view.dryRun, disabled: view.controlsDisabled,
          onChange: (event) => {
            const focused = document.activeElement === event.target;
            controller.setDryRun(event.target.checked, target);
            if (focused) document.getElementById(checkboxId)?.focus();
          },
        }),
        el("span", { class: "sw", "aria-hidden": "true" }),
        el("span", { text: "dry-run" }),
      ]),
      el("div", { class: "fleet-actions" }, commands),
    ]) : el("p", { class: "sec-note", text: "Remote control is not configured." }),
  ]);
}

export function renderFleet(snapshot) {
  const panel = $("#fleet");
  if (!panel) return;
  panel.classList.toggle("is-loading", Boolean(snapshot.fleetLoading));
  const records = snapshot.fleet?.machines || [];
  const machines = [...records];
  const localId = snapshot.fleet?.machine_id || snapshot.state?.machine_id;
  if (localId && !machines.some((machine) => machine.local)) {
    machines.unshift({ machine: localId, local: true });
  }
  const current = records.filter((machine) => machine.current).length;
  const hasProblems = records.some((machine) => machine.error || machine.problems > 0);
  const tone = snapshot.fleetError || hasProblems ? "bad" : records.length && current === records.length ? "ok" : "idle";
  const pill = $("#fleet-verdict");
  pill.className = `pill pill-${tone}`;
  pill.title = "Recorded Store revisions; these cards do not query other machines";
  $("#fleet-verdict-text").textContent = snapshot.fleetLoading ? "Loading" : `${current}/${records.length} current`;
  $("#fleet-meta").textContent = snapshot.fleetLoading ? "loading…" : snapshot.fleet?.at || "";
  const grid = clear($("#fleet-grid"));
  if (snapshot.fleetError) grid.append(el("div", {
    class: "fleet-error fleet-error-block", role: "alert", text: `Fleet unavailable: ${snapshot.fleetError}`,
  }));
  if (!records.length && !snapshot.fleetError) grid.append(el("div", {
    class: "tree-empty", text: snapshot.fleetLoading ? "Loading Machine records…" : "No Machine records yet. Run sync to create this machine’s record.",
  }));
  for (const machine of machines) grid.append(card(machine, controller.view({
    busy: snapshot.busy, loading: snapshot.fleetLoading, machine: machine.local ? null : machine.machine,
  })));
}
