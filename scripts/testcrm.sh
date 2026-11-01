#!/usr/bin/env bash
# testCRM launcher for macOS and Linux: a disposable project with examples/testcrm installed and
# seeded, its backend and the /ui development server, all on loopback. The data is removed on exit.
set -euo pipefail

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [[ $# -ne 0 ]]; then
  echo 'Usage: bash scripts/testcrm.sh   (set EVLET_KEEP_DATA=1 to keep the project directory)' >&2
  exit 2
fi

# Homebrew's versioned Node formula is keg-only. Activate it for this launcher only.
if command -v brew >/dev/null 2>&1; then
  brew_prefix="$(brew --prefix)"
  if [[ -d "$brew_prefix/opt/node@24/bin" ]]; then
    export PATH="$brew_prefix/opt/node@24/bin:$PATH"
  fi
fi

if [[ ! -x .venv/bin/python || ! -f frontend/node_modules/vite/bin/vite.js ]] || ! command -v node >/dev/null 2>&1; then
  echo 'Dependencies are missing. Run: bash scripts/playground.sh --setup' >&2
  exit 1
fi

export EVLET_BACKEND_PORT="${EVLET_BACKEND_PORT:-8010}"
export EVLET_FRONTEND_PORT="${EVLET_FRONTEND_PORT:-5180}"

# Check the ports before creating anything. Never stop another process to free a port.
.venv/bin/python - <<'PY'
import os
import socket
ports = [int(os.environ[name]) for name in ('EVLET_BACKEND_PORT', 'EVLET_FRONTEND_PORT')]
if len(set(ports)) != 2 or any(not 1024 <= port <= 65535 for port in ports):
    raise SystemExit('Use two different unprivileged ports')
for port in ports:
    with socket.socket() as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            connection.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'Port {port} is already in use. Stop the existing server first.')
PY

project="$(mktemp -d "${TMPDIR:-/tmp}/evlet-testcrm.XXXXXX")/project"
backend_pid=''
frontend_pid=''
cleanup() {
  for server_pid in "$frontend_pid" "$backend_pid"; do
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
      wait "$server_pid" 2>/dev/null || true
    fi
  done
  if [[ "${EVLET_KEEP_DATA:-0}" == '1' ]]; then
    echo "Kept $project"
  else
    rm -rf "$(dirname "$project")"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/python -m scripts.testcrm build "$project"
export EVLET_TESTCRM_PROJECT="$project"

.venv/bin/python -m uvicorn scripts.testcrm:create_app --factory --no-proxy-headers --host 127.0.0.1 --port "$EVLET_BACKEND_PORT" > "$project/backend.log" 2>&1 &
backend_pid=$!
node frontend/node_modules/vite/bin/vite.js frontend --host 127.0.0.1 --port "$EVLET_FRONTEND_PORT" --strictPort > "$project/frontend.log" 2>&1 &
frontend_pid=$!

if ! .venv/bin/python - "$backend_pid" "$frontend_pid" <<'PY'
import json
import os
import sys
import time
from urllib.request import urlopen

port = os.environ['EVLET_FRONTEND_PORT']
for attempt in range(150):
    try:
        for pid in sys.argv[1:]:
            os.kill(int(pid), 0)
    except ProcessLookupError:
        raise SystemExit('A development server exited during startup.')
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/auth/session', timeout=1) as response:
            if 'user' in json.load(response):
                break
    except (OSError, ValueError):
        pass
    time.sleep(0.1)
else:
    raise SystemExit('testCRM did not become ready.')
print(f'testCRM is ready at http://127.0.0.1:{port}/ui. Press Control-C to stop.')
PY
then
  tail -n 30 "$project/backend.log" "$project/frontend.log" >&2
  exit 1
fi

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
echo 'A server stopped.' >&2
tail -n 30 "$project/backend.log" "$project/frontend.log" >&2
exit 1
