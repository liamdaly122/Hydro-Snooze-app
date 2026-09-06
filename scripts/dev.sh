#!/usr/bin/env bash
#
# Run the whole thing on this machine, against the simulated unit.
#
#   ./scripts/dev.sh
#
# Sets up Python, installs what it needs, builds the app, and starts the service.
# Safe to run over and over: it skips anything already done.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing $1. $2" >&2
    exit 1
  }
}

need python3 "Install Python 3.11 or newer from python.org."
need node "Install Node from nodejs.org."

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "Python 3.11 or newer is needed. You have $(python3 --version)." >&2
  exit 1
fi

if [ ! -d backend/.venv ]; then
  echo "Setting up Python (once, takes a minute)"
  python3 -m venv backend/.venv
fi
# shellcheck disable=SC1091
. backend/.venv/bin/activate

# Some tools create virtual environments without pip in them.
python -m pip --version >/dev/null 2>&1 || python -m ensurepip --upgrade >/dev/null 2>&1 || {
  echo "This virtual environment has no pip. Delete backend/.venv and run this again." >&2
  exit 1
}

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e './backend[dev]'

if [ ! -d frontend/node_modules ]; then
  echo "Installing the app's dependencies (once, takes a minute)"
  (cd frontend && npm install --silent)
fi

echo "Building the app"
(cd frontend && npm run build --silent)

# The address the phone should use, so it does not have to be guessed.
LAN=""
if command -v ipconfig >/dev/null 2>&1; then
  LAN=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
elif command -v hostname >/dev/null 2>&1; then
  LAN=$(hostname -I 2>/dev/null | awk '{print $1}')
fi

echo
echo "  HydroSnooze is starting against a simulated unit."
echo
echo "  On this machine:  http://localhost:8000"
[ -n "$LAN" ] && echo "  On your phone:    http://$LAN:8000   (same Wi-Fi)"
echo
echo "  Every button press it would have sent is printed below."
echo "  Stop it with Ctrl-C."
echo

cd backend
exec python -m uvicorn hydrosnooze.main:app --host 0.0.0.0 --port 8000
