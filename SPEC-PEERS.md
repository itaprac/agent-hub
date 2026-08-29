# agent-hub — machine federation specification

The Web adapter (`agenthub.webapp`, served by `agent-hub-web`) runs on every Machine, such as `machine-a` and `machine-b`. A UI on either Machine shows synchronization state for all Machines and can run sync/apply remotely. Communication stays inside the trusted private network and is server-to-server: the browser talks only to its local Web adapter.

All `SPEC-WEB.md` constraints apply: standard library only, no build step, offline frontend, system fonts, and no CDN.

## 1. Configuration: committed `config/peers.toml`

```toml
# Web UI base URL by machine ID from hub.toml [machines].
[urls]
machine-a = "https://machine-a.example.ts.net"
machine-b = "http://machine-b.example.ts.net:7338"

# Optional, curl --resolve style: connect to this IP but keep the Host header
# from [urls]. Needed when this machine cannot resolve ts.net names; tailscale
# serve routes plain-HTTP requests by Host, so the name must stay in the URL.
[resolve]
"machine-b.example.ts.net" = "192.0.2.20"
```

Parse with standard-library `tomllib`. A missing file or machine entry disables that peer gracefully: peer endpoints return no remote machines, but keep the local machine. Resolve the current machine ID exactly as `hub.py` does, from hostname through `[machines]` in `hub.toml`.

The shared server-to-server secret is not committed. Load it from `AGENT_HUB_PEER_TOKEN` or `AGENT_HUB_PEER_TOKEN_FILE`, defaulting to `~/.config/agent-hub/peer-token`. The file must have mode `0600`.

## 2. Peer endpoints in the Web adapter

### `GET /api/git`

Return local repository state:

```json
{
  "branch": "main",
  "head": {"sha": "<full>", "short": "<7>", "subject": "...", "date": "<ISO8601>"},
  "dirty": 2,
  "ahead": 0,
  "behind": 1,
  "remote": "origin/main",
  "fetch_error": null
}
```

- `dirty` is the number of `git status --porcelain` entries.
- Before calculating ahead/behind, run `git fetch --quiet` with a 5-second timeout. If it fails, set `fetch_error` to a string and calculate against the last known remote state. `?fetch=0` skips fetch.
- Without a remote, set `remote`, `ahead`, and `behind` to null.

### `GET /api/peers`

Aggregate server-side. Calculate local data directly. For every other `peers.toml` entry, fetch `<url>/api/git?fetch=1` and `<url>/api/status` concurrently in threads, with a 5-second timeout per request. HTTPS peers use normal certificate verification; install the private CA on the machine when an internal certificate is used.

```json
{
  "self": "machine-a",
  "in_sync": true,
  "machines": [
    {
      "machine": "machine-a", "local": true, "online": true, "url": null,
      "git": {"...": "same shape as /api/git"},
      "status": {"exit_code": 0, "problems": 0}
    },
    {
      "machine": "machine-b", "local": false, "online": false,
      "url": "http://machine-b.example.ts.net:7338",
      "error": "timeout", "git": null, "status": null
    }
  ]
}
```

- `status.problems` counts MISSING, DRIFT, STALE, and ERROR checks from the structured package status.
- `in_sync` is true only when every machine is online, every repository has `dirty=0`, `ahead=0`, and `behind=0`, and every HEAD SHA matches. It is null when any machine is offline.

### `POST /api/peers/{machine}/run`

Body: `{"command": "sync"|"apply", "dry_run": bool}`. For the local machine, behave like `/api/run`. For a remote machine, proxy a POST to `<url>/api/run` with `X-Hub-Token` and a 120-second timeout. Pass through `{exit_code, lines}`. Return 404 for an unknown machine ID and 502 with `{error}` when a peer is unreachable.

## 3. Authentication

The browser never receives the peer token. Browser mutations are accepted only for same-origin requests and access to the UI is controlled by the Tailnet ACL or reverse proxy. Server-to-server `POST /api/run` requires `X-Hub-Token`; the token does not authorize file editing, adoption, skill creation, or proxy control. Missing or invalid authentication returns HTTP 401.

## 4. Deployment: `deploy/install.sh`

Since issue #24, `setup.sh` owns the launchd service lifecycle: install, update, and uninstall. `deploy/install.sh` remains only as the legacy fleet installer for rollback until the machine cutover tickets complete.

The legacy script is fleet-specific: it preserves `~/.config/agent-hub/peer-token`, generates the `com.agenthub.web` plist for `web.py --host 127.0.0.1 --port 7337`, optionally maps a tailnet share, verifies HTTP 200, and offers `--uninstall`. New installs use `setup.sh`; optional network exposure is documented in `docs/network-exposure.md`.

## 5. Frontend: Machines dashboard section

- Add a panel above the existing status, with one `/api/peers` card per machine. Show its name, online/offline badge, short SHA, subject, branch, dirty/ahead/behind/problem counts, and a local-machine marker. De-emphasize zero counts and highlight nonzero counts.
- The panel header has a global badge: **In sync** in green; **Diverged** in orange with a short reason such as `machine-b: 2 dirty, behind 1`; or **Unknown** in gray when a machine is offline.
- Each card has Sync and Apply buttons. Remote actions use `/api/peers/{machine}/run`. Send output to the existing log panel, disable the active button with a spinner, and refresh peers afterward.
- Refresh through the global top-bar Refresh and automatically every 60 seconds. Pause automatic refresh during an operation. The panel header holds the shared dry-run checkbox for card actions.
- Match the existing `web/style.css` colors, typography, and badges. Add no external resources. Browser requests never include the peer token.

## 6. Tests

`tests/smoke-peers.sh` follows existing smoke tests. Start `web.py` on two ports with two test-repository copies. Give them different machine IDs by changing hostname mappings or through a minimal environment hook such as `AGENT_HUB_MACHINE` supported by `hub.py` and `web.py`. Point both `peers.toml` files at the corresponding `127.0.0.1:PORT` URLs.

Assert that `/api/peers` sees both machines online, `in_sync` changes after a commit in one copy, remote proxy execution works, and an invalid token returns 401.
