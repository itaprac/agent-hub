// Checks the local-controller seam for views with transient interaction state.
// Node only; local stand-ins isolate controllers from the DOM and backend.

import assert from "node:assert/strict";
import { api } from "../web/js/api.js";
import { adoptProjectField, projectField } from "../web/js/modals.js";

import { createFleetController, machineState, recordAge, renderFleet } from "../web/js/fleet.js";
import { createSettingsController } from "../web/js/settings.js";
import { createUsageController } from "../web/js/usage.js";
import { buildInstructionsTree, buildConfigTree, skillProvenance } from "../web/js/workspace.js";

assert.deepEqual(buildInstructionsTree({ instructions: { global: [
  { name: "AGENTS.md", path: "AGENTS.md", exists: true },
  { name: "codex.md", path: "agents/codex.md", exists: true },
] } }).map((group) => group.files.map((file) => file.path)), [["AGENTS.md"], ["agents/codex.md"]]);
assert.deepEqual(buildConfigTree({ config_files: [
  { path: "hub.toml", name: "hub.toml", exists: false }, { path: "config/peers.toml" },
] })[0].files.map((file) => file.path), ["hub.toml"]);
assert.equal(skillProvenance({ installed: false }), null);
assert.equal(skillProvenance({ installed: true, provenance: { source_url: "javascript:alert(1)" } }).url, null);
assert.equal(skillProvenance({ installed: true, provenance: { source_url: "https://example.com/skill", source: "<script>" } }).source, "<script>");
assert.equal(skillProvenance({ installed: true, provenance: { source_url: "https://example.com/skill" } }).url, "https://example.com/skill");

console.log("== 1. Usage ignores stale requests after the selected window changes ==");
const usageRequests = [];
const publishedUsage = [];
const usageViews = [];
const usageController = createUsageController({
  request(days) {
    return new Promise((resolve, reject) => usageRequests.push({ days, resolve, reject }));
  },
  publish(usage) {
    publishedUsage.push(usage);
  },
  render(view) {
    usageViews.push(view);
  },
});
const staleRefresh = usageController.refresh();
const currentRefresh = usageController.selectDays(7);
assert.deepEqual(usageRequests.map(({ days }) => days), [30, 7]);
assert.equal(usageController.view().loading, true);
usageRequests[1].resolve({ window: 7, settings: {} });
await currentRefresh;
usageRequests[0].resolve({ window: 30, settings: {} });
await staleRefresh;
assert.deepEqual(publishedUsage, [null, { window: 7, settings: {} }]);
assert.deepEqual(usageController.view(), {
  days: 7,
  metric: "cost",
  breakdown: "model",
  loading: false,
  error: null,
});
assert.equal(usageViews.at(-1).loading, false);
assert.equal(usageController.view(), usageController.view());
assert.equal(usageViews.at(-1), usageController.view());
assert.throws(() => {
  usageViews.at(-1).days = 90;
}, TypeError);
assert.equal(usageController.view().days, 7);
console.log("PASS");

console.log("== 2. Settings keeps the token draft after a save failure ==");
const settingsRequests = [];
const publishedSettings = [];
const settingsErrors = [];
const settingsViews = [];
const settingsController = createSettingsController({
  request(patch) {
    return new Promise((resolve, reject) => settingsRequests.push({ patch, resolve, reject }));
  },
  publish(settings) {
    publishedSettings.push(settings);
  },
  render(view) {
    settingsViews.push(view);
  },
  reportError(error) {
    settingsErrors.push(error.message);
  },
});
settingsController.editToken("secret draft");
const failedSave = settingsController.saveToken();
assert.deepEqual(settingsRequests[0].patch, { cursorToken: "secret draft" });
settingsRequests[0].reject(new Error("save failed"));
assert.equal(await failedSave, false);
assert.equal(settingsController.view().tokenDraft, "secret draft");
assert.deepEqual(settingsErrors, ["save failed"]);
assert.equal(settingsViews.at(-1).tokenDraft, "secret draft");

const successfulSave = settingsController.saveToken();
settingsRequests[1].resolve({ cursorTokenSet: true });
assert.equal(await successfulSave, true);
assert.equal(settingsController.view().tokenDraft, "");
assert.deepEqual(publishedSettings, [{ cursorTokenSet: true }]);
assert.equal(settingsViews.at(-1).tokenDraft, "");
assert.equal(settingsController.view(), settingsController.view());
assert.equal(settingsViews.at(-1), settingsController.view());
assert.throws(() => {
  settingsViews.at(-1).tokenDraft = "leaked change";
}, TypeError);
assert.equal(settingsController.view().tokenDraft, "");
console.log("PASS");

console.log("== 3. Fleet keeps dry-run stable and pauses automatic refresh during a command ==");
const fleetRequests = [];
const fleetRuns = [];
const fleetViews = [];
let timerTick = null;
let finishCommand;
const fleetController = createFleetController({
  request() {
    fleetRequests.push(true);
    return Promise.resolve({ machines: [] });
  },
  publish() {},
  render(view) {
    fleetViews.push(view);
  },
  now: () => "12:00",
  isBusy: () => false,
  schedule(callback, delay) {
    assert.equal(delay, 60_000);
    timerTick = callback;
    return 1;
  },
  cancel() {},
});
fleetController.setRunner((command, dryRun) => {
  fleetRuns.push({ command, dryRun });
  return new Promise((resolve) => {
    finishCommand = resolve;
  });
});
fleetController.startAutoRefresh();
fleetController.setDryRun(true);
const command = fleetController.run("apply");
assert.deepEqual(fleetRuns, [{ command: "apply", dryRun: true }]);
fleetController.setDryRun(false);
assert.equal(fleetController.view().dryRun, true);
assert.deepEqual(fleetController.view().running, { command: "apply", dryRun: true, machine: null });
const runningView = fleetController.view();
assert.throws(() => {
  runningView.running.dryRun = false;
}, TypeError);
assert.equal(fleetController.view().running.dryRun, true);
await timerTick();
assert.equal(fleetRequests.length, 0);

finishCommand();
await command;
assert.equal(fleetRequests.length, 1);
await timerTick();
assert.equal(fleetRequests.length, 2);
assert.deepEqual(fleetController.view({ loading: true }), {
  dryRun: true,
  error: null,
  running: null,
  controlsDisabled: true,
});
assert.deepEqual(fleetController.view(), {
  dryRun: true,
  error: null,
  running: null,
  controlsDisabled: false,
});
assert.equal(fleetViews.some((view) => view.running?.command === "apply"), true);
console.log("PASS");

console.log("== 4. Skill forms send checkout paths and boolean adoption scope ==");
const projectChoice = projectField([
  { name: "example.com--team--project", path: "/tmp/project", available: true },
  { name: "missing-project", path: "/tmp/missing", available: false },
]);
assert.deepEqual(projectChoice.options.map((option) => option.value), ["", "/tmp/project"]);
const adoptChoice = adoptProjectField();
assert.deepEqual(adoptChoice.options.map((option) => option.value), ["", "project"]);
const originalFetch = globalThis.fetch;
const payloads = [];
globalThis.fetch = async (url, options) => {
  payloads.push({ url, payload: JSON.parse(options.body) });
  return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ exit_code: 0 }) };
};
try {
  await api.addSkill("private", projectChoice.options[1].value);
  await api.adopt("/tmp/local", adoptChoice.options[0].value, "");
  await api.adopt("/tmp/project/local", adoptChoice.options[1].value, "renamed");
} finally {
  globalThis.fetch = originalFetch;
}
assert.deepEqual(payloads, [
  { url: "/api/add-skill", payload: { name: "private", project: "/tmp/project" } },
  { url: "/api/adopt", payload: { path: "/tmp/local", project: false, name: null } },
  { url: "/api/adopt", payload: { path: "/tmp/project/local", project: true, name: "renamed" } },
]);
console.log("PASS");
console.log("== 5. Fleet coalesces refreshes and accepts only local commands ==");
let resolveFleet;
let fetches = 0;
const snapshots = [];
const isolatedFleet = createFleetController({
  request: () => { fetches++; return new Promise((resolve) => { resolveFleet = resolve; }); },
  publish: (patch) => snapshots.push(patch),
});
const firstFleet = isolatedFleet.refresh();
const secondFleet = isolatedFleet.refresh();
assert.equal(firstFleet, secondFleet);
await Promise.resolve();
assert.equal(fetches, 1);
resolveFleet({ machine_id: "mini", machines: [] });
await firstFleet;
assert.equal(snapshots.at(-1).fleetLoading, false);
assert.equal(await isolatedFleet.run("remote-sync"), false);
assert.deepEqual(machineState({ current: true, problems: 0 }), { tone: "ok", word: "current", rest: "0 problems" });
assert.equal(machineState({ behind: 3, problems: 2 }).word, "behind 3");
assert.equal(machineState({ error: "invalid record" }).tone, "bad");
assert.equal(recordAge(null), "not recorded");
assert.equal(recordAge(7200), "2h ago");
console.log("PASS");

console.log("== 6. Store requests are serialized and a failed request releases the queue ==");
const requests = [];
globalThis.fetch = (url, options) => new Promise((resolve) => requests.push({ url, options, resolve }));
try {
  const stateRequest = api.state();
  const statusRequest = api.status();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests.map((item) => item.url), ["/api/state"]);
  requests[0].resolve({ ok: false, status: 500, headers: { get: () => "application/json" }, json: async () => ({ error: "state failed" }) });
  await assert.rejects(stateRequest, /state failed/);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests.map((item) => item.url), ["/api/state", "/api/status"]);
  requests[1].resolve({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ exit_code: 0 }) });
  await statusRequest;
} finally { globalThis.fetch = originalFetch; }
console.log("PASS");

console.log("== 7. Command payloads preserve the optional machine target ==");
const installPayloads = [];
globalThis.fetch = async (url, options) => {
  installPayloads.push({ url, payload: JSON.parse(options.body) });
  return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ exit_code: 0 }) };
};
try { await api.install("owner/repo", "review"); await api.update(); await api.run("apply", true); await api.run("sync", false, "macbook"); }
finally { globalThis.fetch = originalFetch; }
assert.deepEqual(installPayloads, [
  { url: "/api/run", payload: { command: "install", source: "owner/repo", skill: "review" } },
  { url: "/api/run", payload: { command: "update" } },
  { url: "/api/run", payload: { command: "apply", dry_run: true } },
  { url: "/api/run", payload: { command: "sync", dry_run: false, machine: "macbook" } },
]);
assert.equal(api.peerRun, undefined);
console.log("PASS");
console.log("== 8. Fleet counts records and offers actions only on configured machines ==");
// A small DOM stand-in exercises the rendered cards, not a parallel summary model.
class FleetNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.classList = { toggle() {} };
  }
  append(child) { this.children.push(child); }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const oldDocument = globalThis.document;
const oldNode = globalThis.Node;
const panelNodes = new Map(["fleet", "fleet-verdict", "fleet-verdict-text", "fleet-meta", "fleet-grid"]
  .map((id) => [`#${id}`, new FleetNode("div")]));
globalThis.Node = FleetNode;
globalThis.document = {
  querySelector: (selector) => panelNodes.get(selector),
  createElement: (tag) => new FleetNode(tag),
  createTextNode: (text) => Object.assign(new FleetNode("text"), { textContent: text }),
};
const descendants = (node) => [node, ...node.children.flatMap(descendants)];
const renderedText = (node) => descendants(node).map((child) => child.textContent).join(" ");
const emptyFleet = { busy: 0, fleetLoading: false, fleet: { machine_id: "mini", machines: [] } };
try {
  renderFleet(emptyFleet);
  assert.equal(panelNodes.get("#fleet-verdict-text").textContent, "0/0 current");
  assert.match(renderedText(panelNodes.get("#fleet-grid")), /No Machine records yet. Run sync/);
  let cards = panelNodes.get("#fleet-grid").children.filter((node) => node.tag === "article");
  assert.equal(cards.length, 1);
  assert.match(cards[0].className, /is-local/);
  assert.deepEqual(descendants(cards[0]).filter((node) => node.tag === "button")
    .map((node) => [renderedText(node).trim(), node.disabled]), [["Sync", false], ["Apply", false]]);
  assert.equal(descendants(cards[0]).filter((node) => node.type === "checkbox").length, 1);

  const remote = { machine: "laptop", local: false, current: true, problems: 0, age_seconds: 60 };
  renderFleet({ ...emptyFleet, fleet: { machine_id: "mini", machines: [remote] } });
  assert.equal(panelNodes.get("#fleet-verdict-text").textContent, "1/1 current");
  assert.equal(panelNodes.get("#fleet-verdict").className, "pill pill-ok");
  assert.doesNotMatch(renderedText(panelNodes.get("#fleet-grid")), /No Machine records yet/);
  cards = panelNodes.get("#fleet-grid").children.filter((node) => node.tag === "article");
  assert.equal(cards.length, 2);
  const remoteCard = cards.find((node) => !node.className.includes("is-local"));
  assert.match(renderedText(remoteCard), /laptop/);
  assert.equal(descendants(remoteCard).filter((node) => ["button", "input"].includes(node.tag)).length, 0);
  assert.match(renderedText(remoteCard), /Remote control is not configured/);
  renderFleet({ ...emptyFleet, fleet: { machine_id: "mini", machines: [{ ...remote, remote_control: true }] } });
  const configuredCard = panelNodes.get("#fleet-grid").children.find((node) => node.tag === "article" && !node.className.includes("is-local"));
  const remoteButtons = descendants(configuredCard).filter((node) => node.tag === "button");
  assert.deepEqual(remoteButtons.map((node) => [node.title, node.disabled]), [["Publish this Store, sync on laptop, then refresh its record", false], ["Apply on laptop", false]]);
  assert.equal(descendants(configuredCard).filter((node) => node.type === "checkbox").length, 1);
  assert.doesNotMatch(renderedText(configuredCard), /Remote control is not configured|offline|online/);
} finally {
  if (oldDocument === undefined) delete globalThis.document; else globalThis.document = oldDocument;
  if (oldNode === undefined) delete globalThis.Node; else globalThis.Node = oldNode;
}
console.log("PASS");
console.log("== 9. Remote commands capture target and dry-run until request and refresh finish ==");
const targetedRuns = [];
let resolveRemoteRun;
let resolveRemoteRefresh;
let targetRefreshes = 0;
const targetController = createFleetController({
  canRun: (machine) => machine === null || ["macbook", "workstation"].includes(machine),
  request: () => { targetRefreshes++; return new Promise((resolve) => { resolveRemoteRefresh = resolve; }); },
});
targetController.setRunner((command, dryRun, machine) => {
  targetedRuns.push({ command, dryRun, machine });
  return new Promise((resolve) => { resolveRemoteRun = resolve; });
});
targetController.setDryRun(true, "macbook");
assert.equal(targetController.view().dryRun, false);
assert.equal(targetController.view({ machine: "workstation" }).dryRun, false);
assert.equal(await targetController.run("sync", "unconfigured"), false);
const remoteRun = targetController.run("sync", "macbook");
assert.deepEqual(targetedRuns, [{ command: "sync", dryRun: true, machine: "macbook" }]);
assert.deepEqual(targetController.view().running, { command: "sync", dryRun: true, machine: "macbook" });
assert.equal(targetController.setDryRun(false, "macbook"), false);
assert.equal(targetController.setDryRun(true), false);
assert.equal(await targetController.run("apply", "workstation"), false);
assert.equal(await targetController.run("apply"), false);
resolveRemoteRun({ exit_code: 1, lines: [{ level: "ERROR", text: "SSH connection refused" }] });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(targetRefreshes, 1);
assert.equal(targetController.view().running.machine, "macbook");
assert.equal(targetController.view({ machine: "macbook" }).error, "SSH connection refused");
assert.equal(targetController.view().error, null);
assert.equal(await targetController.run("apply"), false);
resolveRemoteRefresh({ machines: [] });
await remoteRun;
assert.equal(targetController.view().running, null);
assert.equal(targetController.view({ machine: "macbook" }).dryRun, true);
assert.equal(targetController.view().dryRun, false);
const localRun = targetController.run("apply");
assert.deepEqual(targetedRuns.at(-1), { command: "apply", dryRun: false, machine: null });
resolveRemoteRun({ exit_code: 0, lines: [] });
await new Promise((resolve) => setImmediate(resolve));
resolveRemoteRefresh({ machines: [] });
await localRun;
console.log("PASS");
console.log("WEB STATE TEST PASSED");
