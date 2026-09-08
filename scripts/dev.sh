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

need node "Install Node from nodejs.org."

# macOS ships Python 3.9 as `python3` and always has, so a freshly installed 3.12
# may not be what `python3` points at until Terminal is restarted. Look for the
# versioned names too, rather than refusing to run and telling someone to install
# the thing they just installed.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.11 or newer is needed." >&2
  if command -v python3 >/dev/null 2>&1; then
    echo "The python3 on this Mac is $(python3 --version 2>&1)." >&2
  fi
  echo "Install it from python.org/downloads, then quit Terminal (Cmd Q) and open it again." >&2
  exit 1
fi

if [ ! -d backend/.venv ]; then
  echo "Setting up Python (once, takes a minute)"
  "$PYTHON" -m venv backend/.venv
fi
# shellcheck disable=SC1091
. backend/.venv/bin/activate

# Some tools create virtual environments without pip in them.
python -m pip --version >/dev/null 2>&1 || python -m ensurepip --upgrade >/dev/null 2>&1 || {
  echo "This virtual environment has no pip. Delete backend/.venv and run this again." >&2
  exit 1
}

python -m pip install --quiet --upgrade pip
# The hardware extra pulls in aioesphomeapi, which the ESPHome blaster needs.
# It used to be left out here because the Mac only ever ran the simulator, but
# the blaster is driven from the Mac now and a missing library at 2am is a poor
# discovery. The Pi installer has always included it.
python -m pip install --quiet -e './backend[dev,hardware]'

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

# What it is driving decides how much there is worth watching. Against the
# simulator there is one stream and the press log is the whole story. On real
# hardware there are three, and the interesting moments are the ones where they
# disagree, so they are worth having side by side rather than in three tabs.
#
# An environment variable wins over the file, which is the order the service
# itself reads them in.
MODE=$(sed -n 's/^[[:space:]]*HS_TRANSMITTER[[:space:]]*=[[:space:]]*\([a-z]*\).*/\1/p' \
  backend/.env 2>/dev/null | tail -1)
MODE="${HS_TRANSMITTER:-${MODE:-fake}}"

echo
echo "  HydroSnooze is starting."
echo
echo "  On this machine:  http://localhost:8000"
[ -n "$LAN" ] && echo "  On your phone:    http://$LAN:8000   (same Wi-Fi)"
echo
if [ "$MODE" = "esphome" ]; then
  echo "  Driving the REAL unit. Presses land on the hardware."
else
  echo "  Driving the SIMULATED unit. Every press is printed instead of sent."
fi
echo "  Stop it with Ctrl-C."
echo

if [ "$MODE" = "esphome" ]; then
  # The service, the blaster's own log and the plug, tagged and interleaved.
  exec python scripts/watch.py
fi

cd backend
exec python -m uvicorn hydrosnooze.main:app --host 0.0.0.0 --port 8000
