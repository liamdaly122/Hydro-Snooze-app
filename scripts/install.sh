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
# Debian, rsync is what scripts/deploy.sh needs on this end to copy the app
# across from the Mac later, and iw is what turns Wi-Fi power saving off further
# down. iw is here rather than left to chance because its absence was not
# survivable: see the block below.
MISSING=()
"$PYTHON" -c 'import venv' 2>/dev/null || MISSING+=(python3-venv)
command -v rsync >/dev/null 2>&1 || MISSING+=(rsync)
command -v iw >/dev/null 2>&1 || MISSING+=(iw)

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

# --- The clock ----------------------------------------------------------------
#
# Worth checking here rather than discovering at 2am. The scheduler works in
# naive local time, so a Pi left on UTC runs the whole night an hour early
# through British Summer Time, and nothing in the app can tell. A Pi also has no
# battery-backed clock: it only knows the time because it asked the network.

if command -v timedatectl >/dev/null 2>&1; then
  TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  SYNCED=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
  say "Clock: $(date '+%a %d %b %H:%M %Z'), timezone ${TZ_NAME:-unknown}"

  case "$TZ_NAME" in
    UTC | Etc/UTC | "")
      echo "   WARNING: the timezone is UTC, which is almost certainly not what you want."
      echo "   Through British Summer Time the whole night would run an hour early."
      echo "   Fix it:  sudo timedatectl set-timezone Europe/London"
      ;;
  esac

  if [ "$SYNCED" != "yes" ]; then
    echo "   WARNING: the clock has not synchronised with the network yet."
    echo "   A Pi has no battery-backed clock, so until this says yes the time is a guess."
    echo "   Check it:  timedatectl"
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
  #
  # The database path follows PREFIX rather than being written out. It used to be
  # the literal /opt/hydrosnooze inside a quoted heredoc, which could not expand
  # even if it had been a variable, so any install somewhere else wrote its
  # database into the default location.
  {
    echo "HS_TRANSMITTER=fake"
    echo "HS_POWER_MONITOR=fake"
    echo "HS_DB_PATH=$PREFIX/data/hydrosnooze.db"
  } > "$PREFIX/.env"

  echo "   Still simulated. See SETUP.md step 6 for the two lines to change."
fi

# --- The service --------------------------------------------------------------

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$SKIP_SYSTEMD" = "1" ]; then
  say "Skipping systemd (SKIP_SYSTEMD=1)"
  UNIT="$PREFIX/${SERVICE_NAME}.service"
fi

# --- The machine underneath -----------------------------------------------------

if [ "$SKIP_SYSTEMD" != "1" ]; then
  # Wi-Fi power saving is on by default on Raspberry Pi OS, and it is a poor fit
  # for this. Both the plug and the blaster are reached over Wi-Fi, and the
  # symptom of power saving is exactly the one that cost a night on 9 September:
  # intermittent latency and a connection that drops without saying so. The Pi is
  # mains powered and doing nothing else, so there is nothing to save it for.
  #
  # iw applies it now without bouncing the link, which matters because this is
  # usually being run over SSH on that same link. nmcli makes it survive a reboot.
  # The interface is found in /sys rather than with `iw dev`, and that is not
  # tidiness. This block used to begin with `iw dev`, so on a Pi that did not
  # have iw the variable came back empty, the script announced "no wireless
  # interface found", and power saving was left on. Two states that want
  # opposite responses looked identical, and the one that needed action was the
  # one reported as fine.
  #
  # It cost the night of 18 September. The Pi dropped off the network at 01:33
  # and never came back: REM and Wake both missed, the unit still running at
  # breakfast, and the plug, the blaster and the probe board all unreachable at
  # once, which is what sent me looking at three innocent devices.
  #
  # /sys/class/net is the kernel. It needs nothing installed and cannot be
  # missing.
  WLAN=""
  for candidate in /sys/class/net/*/wireless; do
    [ -e "$candidate" ] || continue
    WLAN=$(basename "$(dirname "$candidate")")
    break
  done

  if [ -z "$WLAN" ]; then
    say "No wireless interface, so Wi-Fi power saving was left alone"
    echo "   Expected on a Pi using ethernet, and nothing to do."
  else
    say "Turning off Wi-Fi power saving on $WLAN"
    # Now, without bouncing the link, which matters because this is usually
    # being run over SSH on that same link.
    as_root iw dev "$WLAN" set power_save off 2>/dev/null \
      || echo "   Could not set it now. The permanent setting below still applies from the next boot."

    # Permanent, and deliberately a NetworkManager drop-in rather than
    # `nmcli connection modify`. This Pi's connection is managed by netplan
    # (netplan-wlan0-...), and netplan regenerates the connection profile
    # whenever it is applied, taking any per-connection setting with it. A
    # conf.d default belongs to NetworkManager itself, so it survives that, and
    # it still applies if the connection comes back under a different name.
    if [ -d /etc/NetworkManager ]; then
      as_root mkdir -p /etc/NetworkManager/conf.d
      printf '[connection]\nwifi.powersave = 2\n' \
        | as_root tee /etc/NetworkManager/conf.d/hydrosnooze-powersave.conf >/dev/null
      as_root systemctl reload NetworkManager 2>/dev/null || true
    fi

    # Read back rather than assumed. The whole failure above was a setting that
    # was never applied and never checked, so this one gets checked.
    NOW=$(as_root iw dev "$WLAN" get power_save 2>/dev/null | awk '{print $NF}')
    echo "   Power save on $WLAN is now: ${NOW:-unknown}"
    if [ "$NOW" = "on" ]; then
      echo "   ! Still on. The Pi will drop off the network when it goes idle." >&2
    fi
  fi

  # The journal is this machine's only record of what happened, so it is worth
  # keeping. It is not worth letting it take a tenth of the card, which is the
  # default. Two hundred megabytes is months of this service.
  #
  # Storage=persistent is the line that matters, and it was missing until the
  # 19th. The default on Raspberry Pi OS is Storage=auto, which keeps the
  # journal only if /var/log/journal already exists, and on a fresh Lite image
  # it does not. So the journal lived in a tmpfs and every reboot wiped it.
  #
  # That is the worst possible place to lose a log. A service that restarted and
  # a machine that rebooted look identical from the app and want completely
  # different fixes, and the only thing that tells them apart is a log written
  # before the reboot. Twice now the answer to "why did it go down overnight"
  # has been destroyed by the going down.
  if [ -d /etc/systemd ]; then
    say "Keeping the journal across reboots, capped at 200M"
    as_root mkdir -p /etc/systemd/journald.conf.d
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' \
      | as_root tee /etc/systemd/journald.conf.d/hydrosnooze.conf >/dev/null
    # journald creates this itself once Storage=persistent is set, but only on
    # its next start. Making it here means the very next boot is recorded,
    # rather than the one after.
    as_root mkdir -p /var/log/journal
    as_root systemd-tmpfiles --create --prefix /var/log/journal 2>/dev/null || true
    as_root systemctl restart systemd-journald 2>/dev/null || true
  fi
fi

# A Pi has no battery-backed clock, so at boot it believes it is roughly whenever
# it last shut down. systemd-time-wait-sync is what makes time-sync.target mean
# "the clock has actually been set" rather than "we got as far as trying", and it
# ships disabled. The service holds off on its own account too; this is the
# cheaper half of the same fix, and it costs a few seconds at boot.
if [ "$SKIP_SYSTEMD" != "1" ] && systemctl list-unit-files systemd-time-wait-sync.service >/dev/null 2>&1; then
  if ! systemctl is-enabled --quiet systemd-time-wait-sync.service 2>/dev/null; then
    say "Enabling systemd-time-wait-sync, so the clock is set before the service starts"
    as_root systemctl enable systemd-time-wait-sync.service >/dev/null 2>&1 || true
  fi
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
