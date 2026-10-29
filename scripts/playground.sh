#!/usr/bin/env bash
# One-terminal Playground launcher for macOS and Linux.
set -euo pipefail

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--setup" ) ]]; then
  echo 'Usage: bash scripts/playground.sh [--setup]' >&2
  exit 2
fi

# Homebrew's versioned Node formula is keg-only. Activate it for this launcher
# without changing the user's shell configuration.
if command -v brew >/dev/null 2>&1; then
  brew_prefix="$(brew --prefix)"
  if [[ -d "$brew_prefix/opt/node@24/bin" ]]; then
    export PATH="$brew_prefix/opt/node@24/bin:$PATH"
  fi
  if [[ -d "$brew_prefix/opt/python@3.12/bin" ]]; then
    export PATH="$brew_prefix/opt/python@3.12/bin:$PATH"
  fi
fi

python_command="$(command -v python3.12 || command -v python3 || true)"
if [[ -z "$python_command" ]] || ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo 'Install Python 3.12+ and Node 24 first. With Homebrew: brew install python@3.12 node@24' >&2
  exit 1
fi
"$python_command" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12+ is required")'
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major !== 24 || minor < 15) { console.error("Use Node 24.15+ in the 24.x line"); process.exit(1) }'

export EVLET_BACKEND_PORT="${EVLET_BACKEND_PORT:-8000}"
export EVLET_FRONTEND_PORT="${EVLET_FRONTEND_PORT:-5173}"
export EVLET_PLAYGROUND_HOST="${EVLET_PLAYGROUND_HOST:-127.0.0.1}"
application="${EVLET_APPLICATION:-examples.playground.app:create_app}"

# Check before installing or launching anything. Never stop another process to
# free a port, and never start a second instance over a running Playground.
"$python_command" - <<'PY'
import os
import re
import socket
host = os.environ['EVLET_PLAYGROUND_HOST']
if host not in ('localhost', '127.0.0.1') and not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.localhost', host):
    raise SystemExit('Use a loopback Playground hostname')
ports = [int(os.environ[name]) for name in ('EVLET_BACKEND_PORT', 'EVLET_FRONTEND_PORT')]
if len(set(ports)) != 2 or any(not 1024 <= port <= 65535 for port in ports):
    raise SystemExit('Use two different unprivileged ports')
for port in ports:
    with socket.socket() as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            connection.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'Port {port} is already in use. Stop the existing server before starting this launcher.')
PY

if [[ "${1:-}" == '--setup' ]]; then
  "$python_command" -m venv .venv
  .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
  .venv/bin/python -m pip install --no-build-isolation --no-deps -e .
  npm --prefix frontend ci
fi

if [[ ! -x .venv/bin/python || ! -f frontend/node_modules/vite/bin/vite.js ]]; then
  echo 'Dependencies are missing. Run: bash scripts/playground.sh --setup' >&2
  exit 1
fi

log_directory="examples/playground/.data/logs/$EVLET_FRONTEND_PORT"
mkdir -p "$log_directory"
backend_pid=''
frontend_pid=''
worker_pid=''
cleanup() {
  for server_pid in "$worker_pid" "$frontend_pid" "$backend_pid"; do
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
    fi
  done
  for server_pid in "$worker_pid" "$frontend_pid" "$backend_pid"; do
    if [[ -n "$server_pid" ]]; then
      wait "$server_pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/python -m uvicorn "$application" --factory --no-proxy-headers --host 127.0.0.1 --port "$EVLET_BACKEND_PORT" >> "$log_directory/backend.log" 2>&1 &
backend_pid=$!
# Run Node directly so the PID belongs to Vite rather than an npm wrapper.
node frontend/node_modules/vite/bin/vite.js frontend --host 127.0.0.1 --port "$EVLET_FRONTEND_PORT" --strictPort >> "$log_directory/frontend.log" 2>&1 &
frontend_pid=$!

if ! .venv/bin/python - "$backend_pid" "$frontend_pid" <<'PY'
import json
import os
import sys
import time
from urllib.request import Request, urlopen

port = os.environ['EVLET_FRONTEND_PORT']
host = os.environ['EVLET_PLAYGROUND_HOST']
def request(path):
    return urlopen(Request(f'http://127.0.0.1:{port}{path}', headers={'Host': f'{host}:{port}'}), timeout=1)

for attempt in range(100):
    try:
        for pid in sys.argv[1:]:
            os.kill(int(pid), 0)
    except ProcessLookupError:
        raise SystemExit('A development server exited during startup.')
    try:
        with request('/health') as response:
            healthy = json.load(response) == {'status': 'ok'}
        with request('/api/auth/session') as response:
            session = json.load(response)
        if healthy and 'user' in session:
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.1)
else:
    raise SystemExit('The Playground did not become ready.')
print('Playground is ready. Keep this terminal open; press Control-C to stop.')
print(f'Browser address: http://{host}:{port}')
PY
then
  tail -n 30 "$log_directory/backend.log" "$log_directory/frontend.log" >&2
  exit 1
fi

if [[ "${EVLET_RUN_JOBS:-1}" == '1' ]]; then
  .venv/bin/python -m examples.playground.worker >> "$log_directory/worker.log" 2>&1 &
  worker_pid=$!
fi

if [[ "${EVLET_OPEN_BROWSER:-1}" != '0' ]]; then
  if [[ "$(uname -s)" == 'Darwin' ]]; then
    open "http://$EVLET_PLAYGROUND_HOST:$EVLET_FRONTEND_PORT" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://$EVLET_PLAYGROUND_HOST:$EVLET_FRONTEND_PORT" >/dev/null 2>&1 || true
  fi
fi

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  if [[ -n "$worker_pid" ]] && ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "The job worker stopped. Check $log_directory/worker.log." >&2
    exit 1
  fi
  sleep 1
done
echo "A server stopped. Check $log_directory/backend.log and $log_directory/frontend.log." >&2
exit 1
