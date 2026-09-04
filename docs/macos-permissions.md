# macOS file access for Timer and Console

`agent-hub timer on` installs a user LaunchAgent that runs Sync every ten minutes.
`agent-hub ui --service on` installs a separate Console LaunchAgent. The foreground
command, `agent-hub ui`, does not need either service.

macOS can block a background process from reading protected folders even when
that command works in Terminal. Full Disk Access is controlled in System
Settings > Privacy & Security. The operator must grant access; the App cannot
grant it itself. See [Apple's file-access settings](https://support.apple.com/guide/mac-help/mchl211c911f/mac).

## Find the failing process

| Signal | Check |
|---|---|
| Sync does not run | `agent-hub timer status` |
| Console does not respond | `agent-hub ui --service status` |
| Sync reports `Operation not permitted` | `~/Library/Logs/agent-hub-sync.error.log` and `~/Library/Logs/agent-hub-sync.log` |
| Console reports `Operation not permitted` | `~/Library/Logs/agent-hub-web.error.log` and `~/Library/Logs/agent-hub-web.log` |

Inspect the installed jobs to find the executable that launchd starts. This works
with both `uv tool install` and `pipx install`; it does not assume an App checkout
or a `.venv` directory.

```bash
python3 - <<'PY'
from pathlib import Path
import plistlib
import shlex

for label in ("com.agenthub.sync", "com.agenthub.web"):
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not path.is_file():
        continue
    with path.open("rb") as handle:
        job = plistlib.load(handle)
    executable = Path(job["ProgramArguments"][0]).resolve()
    print(f"{label}: {executable}")
    with executable.open("rb") as handle:
        first_line = handle.readline().decode("utf-8", errors="replace").strip()
    if first_line.startswith("#!"):
        interpreter = shlex.split(first_line[2:])[0]
        print(f"  interpreter: {Path(interpreter).resolve()}")
    print(f"  errors: {job.get('StandardErrorPath', 'not configured')}")
PY
```

## Restore access

1. Find the Python interpreter path with the command above.
2. Open System Settings > Privacy & Security > Full Disk Access.
3. Add that binary. Use Cmd+Shift+G in the file picker to enter its path. Remove
   entries for interpreters that are no longer installed.
4. Restart the affected job with the same Store selection that you used when
   installing it:

```bash
agent-hub timer off
agent-hub timer on

agent-hub ui --service off
agent-hub ui --service on
```

For a custom Store, add `--store /absolute/path/to/store` to those commands.
Restart only the jobs you use. Check their status and error logs again.
A grant for Terminal alone does not prove that the interpreter started by
launchd has access. Apple discusses this distinction for background tools in
[its developer forum](https://developer.apple.com/forums/thread/118508).

## After changing Python

A Homebrew Python executable can resolve to a versioned Cellar path. An upgrade
can change that path. A replacement interpreter can also change the identity
that macOS uses for access control. If a job stops working after an upgrade,
inspect its installed executable and grant access to the current interpreter.

After reinstalling the App with a different interpreter, turn each installed job
off and on with the new `agent-hub` command. This refreshes the executable path
in its LaunchAgent. Do not remove the Store or rebuild an unrelated checkout.
