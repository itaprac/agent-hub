// Checks the local-controller seam for views with transient interaction state.
// Node only; local stand-ins isolate controllers from the DOM and backend.

import assert from "node:assert/strict";
import { api } from "../web/js/api.js";
import { adoptProjectField, projectField } from "../web/js/modals.js";

import { createPeersController } from "../web/js/peers.js";
import { createSettingsController } from "../web/js/settings.js";
import { createUsageController } from "../web/js/usage.js";

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

console.log("== 3. Peers keeps dry-run stable and pauses automatic refresh during a command ==");
const peerRequests = [];
const peerRuns = [];
const peerViews = [];
let timerTick = null;
let finishCommand;
const peersController = createPeersController({
  request() {
    peerRequests.push(true);
    return Promise.resolve({ machines: [] });
  },
  publish() {},
  render(view) {
    peerViews.push(view);
  },
  remember() {},
  now: () => "12:00",
  isBusy: () => false,
  isSupported: () => true,
  schedule(callback, delay) {
    assert.equal(delay, 60_000);
    timerTick = callback;
    return 1;
  },
  cancel() {},
});
peersController.setRunner((machine, command, dryRun) => {
  peerRuns.push({ machine, command, dryRun });
  return new Promise((resolve) => {
    finishCommand = resolve;
  });
});
peersController.startAutoRefresh();
peersController.setDryRun(true);
const command = peersController.run("mini", "apply");
assert.deepEqual(peerRuns, [{ machine: "mini", command: "apply", dryRun: true }]);
peersController.setDryRun(false);
assert.equal(peersController.view().dryRun, true);
assert.deepEqual(peersController.view().running, { machine: "mini", command: "apply", dryRun: true });
const runningView = peersController.view();
assert.throws(() => {
  runningView.running.dryRun = false;
}, TypeError);
assert.equal(peersController.view().running.dryRun, true);
await timerTick();
assert.equal(peerRequests.length, 0);

finishCommand();
await command;
assert.equal(peerRequests.length, 1);
await timerTick();
assert.equal(peerRequests.length, 2);
assert.deepEqual(peersController.view({ loading: true }), {
  dryRun: true,
  running: null,
  controlsDisabled: true,
});
assert.deepEqual(peersController.view(), {
  dryRun: true,
  running: null,
  controlsDisabled: false,
});
assert.equal(peerViews.some((view) => view.running?.machine === "mini"), true);
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
console.log("WEB STATE TEST PASSED");
