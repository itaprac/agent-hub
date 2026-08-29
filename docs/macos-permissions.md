# macOS file access for the App service

macOS records a file-access grant against the resolved interpreter path. With a
Homebrew Python, that path contains the Cellar version
(`/opt/homebrew/Cellar/python@3.14/<version>/…/bin/python3.14`). Every
`brew upgrade` of Python changes the path. The old grant does not apply to the
new path. launchd then tries and fails to restart the App service.

## Detect

| Signal | Where |
|---|---|
| Web UI unreachable at `http://127.0.0.1:7337/` | browser |
| `PermissionError: [Errno 1] Operation not permitted` | `~/Library/Logs/agent-hub-web.error.log` |
| "did not return HTTP 200" plus the re-grant steps | `./setup.sh` and `./setup.sh --update` |

## Fix

1. Find the resolved interpreter: `readlink -f <app>/.venv/bin/python`.
2. Open System Settings > Privacy & Security > Full Disk Access.
3. Add that binary. Press Cmd+Shift+G in the file picker to type the path. Remove entries for old versions.
4. Restart the service: `launchctl kickstart -k gui/$(id -u)/com.agenthub.web`.

Add the binary by path. The bundle entry (`org.python.python`) does not grant
access to the interpreter that launchd starts.

## Prevent

Use an interpreter whose path does not change on upgrade, for example the
python.org installer (`/Library/Frameworks/Python.framework/Versions/3.x/bin/python3`).
To switch, rebuild the App environment with it:

```bash
rm -rf <app>/.venv
AGENT_HUB_SETUP_PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 ./setup.sh
```

Then grant Full Disk Access to that binary once.
