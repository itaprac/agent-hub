// Thin wrapper around the local console JSON API. Every call rejects with a readable Error.

function buildHeaders(method, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return Object.keys(headers).length ? headers : undefined;
}

async function performRequest(method, url, body) {
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: buildHeaders(method, body),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new Error(`cannot reach the agent-hub server (${error.message})`);
  }

  const type = response.headers.get("content-type") || "";
  let payload = null;
  if (type.includes("json")) {
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error(`invalid JSON response: ${error.message}`);
    }
  }
  if (!response.ok) {
    const detail = payload && payload.error ? payload.error : `${response.status} ${response.statusText}`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return payload;
}

// Store endpoints share one server lock. Queue them across all UI controllers.
let storeQueue = Promise.resolve();
function request(method, url, body) {
  if (url.startsWith("/api/usage")) return performRequest(method, url, body);
  const pending = storeQueue.then(() => performRequest(method, url, body));
  storeQueue = pending.catch(() => {});
  return pending;
}

export const api = {
  state: () => request("GET", "/api/state"),
  status: () => request("GET", "/api/status"),
  usage: (days = 30) => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Warsaw";
    return request("GET", `/api/usage?days=${encodeURIComponent(days)}&tz=${encodeURIComponent(zone)}`);
  },
  usageSettings: () => request("GET", "/api/usage/settings"),
  saveUsageSettings: (payload) => request("PUT", "/api/usage/settings", payload),
  run: (command, dryRun, machine) => request("POST", "/api/run", { command, dry_run: Boolean(dryRun), ...(machine ? { machine } : {}) }),
  fleet: () => request("GET", "/api/fleet"),
  install: (source, skill) => request("POST", "/api/run", { command: "install", source, ...(skill ? { skill } : {}) }),
  update: (names) => request("POST", "/api/run", { command: "update", ...(names?.length ? { names } : {}) }),
  addSkill: (name, project) => request("POST", "/api/add-skill", { name, project: project || null }),
  adopt: (path, project, name) =>
    request("POST", "/api/adopt", { path, project: Boolean(project), name: name || null }),
  readFile: (path) => request("GET", `/api/file?path=${encodeURIComponent(path)}`),
  writeFile: (path, content, revision) => request("PUT", "/api/file", { path, content, revision }),
  deleteFile: (path, revision) => request("DELETE", "/api/file", { path, revision }),
};
