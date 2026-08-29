#!/bin/sh

if [ -z "${BASH_VERSION:-}" ]; then
    if ! command -v bash >/dev/null 2>&1; then
        echo "[ERROR] Bash is required. Install Bash from https://www.gnu.org/software/bash/, then run setup again." >&2
        exit 1
    fi
    exec bash "$0" "$@"
fi

set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
    echo "[ERROR] Git is required. Install Git from https://git-scm.com/downloads, then run setup again." >&2
    exit 1
fi

APP_ROOT="$(CDPATH= cd -- "${0%/*}" && pwd -P)"
PYTHON=""
if [ -n "${AGENT_HUB_SETUP_PYTHON:-}" ]; then
    PYTHON_CANDIDATES=("$AGENT_HUB_SETUP_PYTHON")
else
    PYTHON_CANDIDATES=(python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3)
fi
for candidate in "${PYTHON_CANDIDATES[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
        'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3.11 or newer is required. Install it from https://www.python.org/downloads/, then run setup again." >&2
    exit 1
fi

export AGENT_HUB_SETUP_APP_ROOT="$APP_ROOT"
export AGENT_HUB_SETUP_PYTHON="$PYTHON"
export AGENT_HUB_SETUP_PLATFORM="$(uname -s)"
export PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m agenthub.setup "$@"
