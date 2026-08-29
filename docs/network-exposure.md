# Network exposure (optional)

The Web UI binds to `127.0.0.1:7337` and has **no general-purpose authentication**. Keep it on localhost or a trusted private network. Do not expose it to the public Internet.

Setup never configures network exposure. Both recipes below are manual and optional.

## Tailscale Serve

Share the UI inside your tailnet only:

```bash
tailscale serve --bg --https=8443 7337
# open https://<machine-name>.<your-tailnet>.ts.net:8443/
```

Turn it off with `tailscale serve --https=8443 off`. Restrict who can reach the port with Tailscale ACLs.

## Reverse proxy

Any reverse proxy works. Example with Caddy on a private network:

```text
hub.internal.example {
    reverse_proxy 127.0.0.1:7337
}
```

Add authentication at the proxy (for example `basic_auth` or a forward-auth provider) if anyone other than you can reach it.

## Peer traffic

Machine-to-machine Peer calls authenticate with the shared token in `~/.config/agent-hub/peer-token` (mode 600). This protects the Peer API only; it does not protect the browser UI.
