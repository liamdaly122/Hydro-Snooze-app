#!/usr/bin/env bash
#
# Install HydroSnooze on the Raspberry Pi. Run this ON the Pi, over SSH.
#
#   ssh liam@hydrosnooze.local
#   git clone https://github.com/liamdaly122/Hydro-Snooze-app.git
#   cd Hydro-Snooze-app
#   ./scripts/install.sh
#
# Safe to run again later. It never overwrites .env, so captured infrared codes
# and calibrated power thresholds survive a reinstall.
#
set -euo pipefail

PREFIX="${PREFIX:-/opt/hydrosnooze}"
SERVICE_NAME="${SERVICE_NAME:-hydrosnooze}"
RUN_USER="${SUDO_USER:-$(id -un)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Set SKIP_SYSTEMD=1 to build everything without touching systemd, which is how
# this gets tested somewhere that is not a Pi.
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"

say() { printf '\n== %s\n' "$1"; }

as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

# --- Python -------------------------------------------------------------------

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
  echo "Raspberry Pi OS Bookworm ships 3.11. On an older release:" >&2
  echo "  sudo apt update && sudo apt install -y python3.11 python3.11-venv" >&2
  exit 1
fi

say "Using $($PYTHON --version)"

# Packages Raspberry Pi OS Lite may not have. venv is separate from python on
# Debian, and rsync is what scripts/deploy.sh needs on this end to copy the app
# across from the Mac later.
MISSING=()
"$PYTHON" -c 'import venv' 2>/dev/null || MISSING+=(python3-venv)
command -v rsync >/dev/null 2>&1 || MISSING+=(rsync)

if [ ${#MISSING[@]} -gt 0 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    say "Installing ${MISSING[*]}"
    as_root apt-get update -qq
    as_root apt-get install -y -qq "${MISSING[@]}"
  else
    echo "Missing: ${MISSING[*]}. Install them and run this again." >&2
    [ "${SKIP_SYSTEMD:-0}" = "1" ] || exit 1
  fi
fi

# --- Layout -------------------------------------------------------------------

say "Setting up $PREFIX"
as_root mkdir -p "$PREFIX" "$PREFIX/static" "$PREFIX/data"
as_root chown -R "$RUN_USER" "$PREFIX"

say "Copying the backend"
# tar rather than rsync, because this has to work on a fresh Pi OS Lite before
# anything has been installed. Wiping first gives the same result as --delete,
# and the destination only ever holds copied source.
rm -rf "$PREFIX/backend"
mkdir -p "$PREFIX/backend"
tar -C "$ROOT/backend" \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='data' \
  --exclude='*.db' \
  -cf - . | tar -C "$PREFIX/backend" -xf -

if [ ! -d "$PREFIX/venv" ]; then
  say "Creating the Python environment (a few minutes on a Pi Zero)"
  "$PYTHON" -m venv "$PREFIX/venv"
fi

say "Installing dependencies"
"$PREFIX/venv/bin/python" -m pip install --quiet --upgrade pip
# The hardware extra pulls in aioesphomeapi, which is only needed once the
# infrared blaster exists. Installing it here means the swap really is a config
# change with nothing left to fetch.
"$PREFIX/venv/bin/python" -m pip install --quiet -e "$PREFIX/backend[hardware]"

# --- Configuration ------------------------------------------------------------

if [ -f "$PREFIX/.env" ]; then
  say "Keeping the existing .env"
  echo "   Captured codes and calibrated thresholds left alone."
else
  say "Creating $PREFIX/.env from the example"
  # systemd's EnvironmentFile cannot cope with comments containing '=' or with
  # quoting, so write a plain one rather than copying .env.example verbatim.
  cat > "$PREFIX/.env" <<'ENVEOF'
HS_TRANSMITTER=fake
HS_POWER_MONITOR=fake
HS_DB_PATH=/opt/hydrosnooze/data/hydrosnooze.db
ENVEOF
  echo "   Still simulated. See SETUP.md step 6 for the two lines to change."
fi

# --- The service --------------------------------------------------------------

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$SKIP_SYSTEMD" = "1" ]; then
  say "Skipping systemd (SKIP_SYSTEMD=1)"
  UNIT="$PREFIX/${SERVICE_NAME}.service"
fi

say "Writing $UNIT"
UNIT_BODY=$(sed -e "s|__USER__|$RUN_USER|g" -e "s|__PREFIX__|$PREFIX|g" "$ROOT/docs/hydrosnooze.service")

if [ "$SKIP_SYSTEMD" = "1" ]; then
  printf '%s\n' "$UNIT_BODY" > "$UNIT"
else
  printf '%s\n' "$UNIT_BODY" | as_root tee "$UNIT" >/dev/null
  as_root systemctl daemon-reload
  as_root systemctl enable "$SERVICE_NAME"
  as_root systemctl restart "$SERVICE_NAME"

  sleep 3
  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo >&2
    echo "The service did not start. What went wrong:" >&2
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager >&2
    exit 1
  fi
fi

# --- Done ---------------------------------------------------------------------

if [ ! -f "$PREFIX/static/index.html" ]; then
  say "No app copied across yet"
  echo "   The service is running but has no pages to serve."
  echo "   From the Mac, in the project folder:  ./scripts/deploy.sh"
else
  say "Done"
fi

echo
echo "  Open this on the phone:  http://$(hostname).local:8000"
echo
echo "  Watch what it is doing:  journalctl -u $SERVICE_NAME -f"
echo "  Stop it:                 sudo systemctl stop $SERVICE_NAME"
echo "  Start it:                sudo systemctl start $SERVICE_NAME"
echo
