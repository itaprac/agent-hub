// Checks the local-controller seam for views with transient interaction state.
// Node only; reducers stay independent from the DOM and the backend.

import assert from "node:assert/strict";

import {
  initialPeersViewState,
  initialSettingsViewState,
  initialUsageViewState,
  projectPeersView,
  reducePeersView,
  reduceSettingsView,
  reduceUsageView,
} from "../web/js/view-state.js";

console.log("== 1. Usage ignores stale requests after the selected window changes ==");
let usage = reduceUsageView(initialUsageViewState, { type: "request-started" });
const staleRequest = usage.requestId;
usage = reduceUsageView(usage, { type: "select-days", days: 7 });
usage = reduceUsageView(usage, { type: "request-started" });
const currentRequest = usage.requestId;
assert.equal(usage.days, 7);
assert.equal(usage.loading, true);
assert.equal(
  reduceUsageView(usage, { type: "request-failed", requestId: staleRequest, error: "late failure" }),
  usage,
);
usage = reduceUsageView(usage, { type: "request-finished", requestId: currentRequest });
assert.equal(usage.loading, false);
assert.equal(usage.error, null);
console.log("PASS");

console.log("== 2. Settings keeps the token draft after a save failure ==");
let settings = reduceSettingsView(initialSettingsViewState, { type: "edit-token", value: "secret draft" });
const unchanged = reduceSettingsView(settings, { type: "save-failed" });
assert.equal(unchanged, settings);
assert.equal(unchanged.tokenDraft, "secret draft");
settings = reduceSettingsView(settings, { type: "save-finished" });
assert.equal(settings.tokenDraft, "");
console.log("PASS");

console.log("== 3. Peers keeps dry-run stable through a running command and rerender ==");
let peers = reducePeersView(initialPeersViewState, { type: "set-dry-run", value: true });
peers = reducePeersView(peers, { type: "command-started", machine: "mini", command: "sync" });
assert.deepEqual(peers.running, { machine: "mini", command: "sync", dryRun: true });
const attemptedChange = reducePeersView(peers, { type: "set-dry-run", value: false });
assert.equal(attemptedChange, peers);
peers = reducePeersView(peers, { type: "command-finished" });
assert.equal(peers.dryRun, true);
assert.equal(peers.running, null);
assert.deepEqual(projectPeersView(peers, { busy: 0, loading: true }), {
  dryRun: true,
  running: null,
  controlsDisabled: true,
});
assert.deepEqual(projectPeersView(peers, { busy: 0, loading: false }), {
  dryRun: true,
  running: null,
  controlsDisabled: false,
});
console.log("PASS");

console.log("WEB STATE TEST PASSED");
