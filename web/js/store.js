// Minimal observable store: one object, coarse notifications, no dependencies.

const listeners = new Set();

export const store = {
  state: null, // GET /api/state payload
  stateError: null, // config error string, if any
  status: null, // last GET /api/status result
  log: null, // last command result: {command, exit_code, lines, at}
  peers: null, // last GET /api/peers payload: {self, in_sync, machines, at}
  peersSupported: true, // false once /api/peers answers 404 (older backend)
  peersError: null, // transport error for the peers panel, if any
  peersLoading: true, // first paint shows machine cards while /api/peers is in flight
  tab: "status",
  filter: "all",
  usage: null, // last GET /api/usage payload
  usageSettings: null,
  busy: 0,
};

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function update(patch) {
  Object.assign(store, patch);
  for (const listener of listeners) listener(store);
}

export async function withBusy(task) {
  update({ busy: store.busy + 1 });
  try {
    return await task();
  } finally {
    update({ busy: Math.max(0, store.busy - 1) });
  }
}

export function projectNames() {
  return (store.state?.projects || []).map((project) => project.name);
}

export function agentNames() {
  return (store.state?.agents || []).map((agent) => agent.name);
}
