# Network exposure (optional)

The Console binds to `127.0.0.1:7337` and has **no authentication**. Keep it on localhost or a trusted private network. Do not expose it to the public Internet.

The App never configures network exposure. Both recipes below are manual and optional.

## Tailscale Serve

Share the Console inside your tailnet only:

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

## Origin on your own Machine

Machines never talk to each other through the App. If you do not want a Git host, make an always-on Machine the Origin over SSH, for example through Tailscale:

```bash
# on the always-on machine, once
git -C ~/.agents config receive.denyCurrentBranch updateInstead
# on every other machine
agent-hub init --from ssh://user@machine.tailnet.ts.net/~/.agents
```
