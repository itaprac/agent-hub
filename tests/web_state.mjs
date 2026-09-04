// Checks the local-controller seam for views with transient interaction state.
// Node only; local stand-ins isolate controllers from the DOM and backend.

import assert from "node:assert/strict";
import { api } from "../web/js/api.js";
import { adoptProjectField, projectField } from "../web/js/modals.js";

import { createFleetController, machineState, recordAge } from "../web/js/fleet.js";
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
assert.deepEqual(fleetController.view().running, { command: "apply", dryRun: true });
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
  running: null,
  controlsDisabled: true,
});
assert.deepEqual(fleetController.view(), {
  dryRun: true,
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

console.log("== 7. Install and Update use local command payloads ==");
const installPayloads = [];
globalThis.fetch = async (url, options) => {
  installPayloads.push({ url, payload: JSON.parse(options.body) });
  return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ exit_code: 0 }) };
};
try { await api.install("owner/repo", "review"); await api.update(); await api.run("apply", true); }
finally { globalThis.fetch = originalFetch; }
assert.deepEqual(installPayloads, [
  { url: "/api/run", payload: { command: "install", source: "owner/repo", skill: "review" } },
  { url: "/api/run", payload: { command: "update" } },
  { url: "/api/run", payload: { command: "apply", dry_run: true } },
]);
assert.equal(api.peerRun, undefined);
console.log("PASS");
console.log("WEB STATE TEST PASSED");
