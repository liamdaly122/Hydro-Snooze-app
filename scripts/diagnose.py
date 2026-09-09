#!/usr/bin/env python3
"""Collect everything needed to work out what went wrong, into one file.

    ./scripts/diagnose.py                    # on the Mac
    ./scripts/diagnose.py --hours 24         # more history

Written for the moment something has failed overnight and the useful thing is
not to explain what happened but to hand over the evidence. It gathers the
service's own log, its events, what it believes, what the plug measured, whether
systemd has been restarting it, and the state of the machine underneath.

Secrets are masked before anything is written. The infrared key, the
notification topic and any Wi-Fi password never reach the file, because the
whole point is that the file gets pasted to someone.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Anything whose name matches this has its value replaced. Matching on the name
#: rather than the value, because a secret that has not been guessed yet still
#: needs hiding, and a value-based filter can only redact what it already knows.
#: Settings safe to print in full. Everything not on this list is masked.
#:
#: The rule used to be the other way round: mask anything whose name looked
#: secret, print the rest. That failed the first time it was tried on a real
#: file. HS_HEARTBEAT_URL does not contain KEY or TOKEN or SECRET, so a file
#: whose entire purpose is to be pasted to a stranger printed, in full, the URL
#: that silences the alarm. Anyone holding it can ping healthchecks.io and keep
#: the check green through a night the Pi spent dead.
#:
#: Masking by default is the only version that stays correct. A setting added
#: next year is secret until someone decides otherwise, rather than public until
#: someone remembers.
SAFE = frozenset(
    {
        "HS_TRANSMITTER",
        "HS_POWER_MONITOR",
        "HS_ESPHOME_HOST",
        "HS_ESPHOME_PORT",
        "HS_ESPHOME_BUTTON_SERVICE",
        "HS_SHELLY_HOST",
        "HS_NTFY_SERVER",
        "HS_DB_PATH",
        "HS_STATIC_DIR",
        "HS_LOG_LEVEL",
        "HS_OFF_THRESHOLD_W",
        "HS_IDLE_MAX_W",
        "HS_COOLING_MAX_W",
        "HS_POWER_SAMPLE_SECONDS",
        "HS_COMMAND_GAP_MS",
        "HS_SAVE_WAIT_MS",
        "HS_ARM_WAIT_S",
        "HS_POWER_SETTLE_S",
        "HS_MAX_TEMPERATURE_C",
        "HS_SIM_SPEED",
        "HS_SIM_AUTO_APPLY_FROM_ANY_PHASE",
    }
)


def mask(value: str) -> str:
    if not value:
        return ""
    return f"{value[:3]}...{value[-2:]} ({len(value)} chars)" if len(value) > 8 else "set"


def deployed_at(prefix: Path = Path("/opt/hydrosnooze/backend")) -> str:
    """When the code that is actually running was last copied across.

    There is no git history in /opt/hydrosnooze, because deploy.sh rsyncs rather
    than pulls. The newest file in it is the next best answer, and it is enough
    to tell whether a deploy landed.
    """
    if not prefix.exists():
        return f"({prefix} does not exist, so nothing is deployed there)"
    newest = max(
        (f.stat().st_mtime for f in prefix.rglob("*.py")),
        default=None,
    )
    if newest is None:
        return f"(no Python found in {prefix})"
    return f"{prefix}, last deployed {datetime.fromtimestamp(newest):%a %d %b %H:%M}"


def run(*command: str, timeout: int = 20) -> str:
    """A shell command's output, or a note saying why there is none."""
    if shutil.which(command[0]) is None:
        return f"({command[0]} not on this machine)"
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except Exception as exc:  # noqa: BLE001
        return f"(failed: {exc})"
    return (done.stdout + done.stderr).strip() or "(no output)"


def fetch(url: str, timeout: int = 5) -> object:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return f"(could not reach {url}: {exc})"


def env_file(path: Path) -> list[str]:
    """The settings, with every secret masked by name."""
    if not path.exists():
        return [f"({path} does not exist)"]
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.split("#")[0].strip().strip("\"'")
        out.append(f"{key}={value if key.upper() in SAFE else mask(value)}")
    return out or ["(no settings set)"]


def section(title: str, body: object) -> str:
    if isinstance(body, (dict, list)):
        body = json.dumps(body, indent=2, default=str)
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}\n{body}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=12, help="how much log to include")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--out", default=None)
    parser.add_argument("--service", default="hydrosnooze")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    now = datetime.now()
    out = Path(args.out) if args.out else Path.home() / f"hydrosnooze-diagnosis-{now:%Y%m%d-%H%M}.txt"

    parts = [
        f"HydroSnooze diagnosis\nCollected {now:%a %d %b %Y %H:%M:%S %Z} on {socket.gethostname()}",
        section(
            "THE MACHINE",
            f"{platform.platform()}\nPython {sys.version.split()[0]}\n"
            f"Local time  {datetime.now().astimezone():%a %d %b %H:%M %Z %z}\n\n"
            f"{run('timedatectl')}",
        ),
        section(
            "VERSION",
            "This clone:  "
            + run("git", "-C", str(ROOT), "log", "-1", "--format=%h %ad %s").strip()
            + "\n"
            # Not the same thing, and the difference has already caused confusion.
            # deploy.sh rsyncs from the Mac's working tree straight into
            # /opt/hydrosnooze, so the code that is running can be newer than the
            # clone this script is reading the git log from. Reporting only the
            # clone means answering "what version are you running" wrongly, which
            # is a poor way to start debugging.
            + "\nRunning:     " + deployed_at(),
        ),
        # First, because a service that has been restarting is the whole answer.
        section(
            "HAS IT BEEN RESTARTING",
            run(
                "systemctl", "show", args.service,
                "-p", "NRestarts", "-p", "ActiveState", "-p", "SubState",
                "-p", "ActiveEnterTimestamp", "-p", "WatchdogTimestamp", "-p", "ExecMainStartTimestamp",
            ),
        ),
        section("SERVICE STATUS", run("systemctl", "status", args.service, "--no-pager", "-l")),
        section("WHAT IT BELIEVES NOW", fetch(f"{base}/api/state")),
        section("DEVICE HEALTH", fetch(f"{base}/api/health")),
        section("SETTINGS (secrets masked)", "\n".join(env_file(ROOT / "backend" / ".env"))),
        section(
            "SETTINGS ON THE PI (secrets masked)",
            "\n".join(env_file(Path("/opt/hydrosnooze/.env"))),
        ),
        section("ITS OWN EVENTS", fetch(f"{base}/api/events?limit=150")),
        section("POWER THE PLUG MEASURED", fetch(f"{base}/api/power?hours={args.hours}")),
        section(
            f"THE LOG, LAST {args.hours} HOURS",
            run("journalctl", "-u", args.service, "--since", f"{args.hours} hours ago",
                "--no-pager", "-n", "2000"),
        ),
        section("DISK", run("df", "-h")),
        section("MEMORY", run("free", "-h")),
        section("WI-FI", run("iwconfig")),
        # The commonest reason a Pi behaves as though the software is broken, and
        # the one that leaves no other trace. "throttled=0x0" is the good answer;
        # anything else and the power supply is the first thing to change.
        section("POWER AND HEAT", run("vcgencmd", "get_throttled")),
        section("TEMPERATURE", run("vcgencmd", "measure_temp")),
    ]

    text = "\n".join(str(p) for p in parts)
    out.write_text(text)

    print()
    print(f"Written to  {out}")
    print(f"            {len(text.splitlines())} lines, {out.stat().st_size // 1024} KB")
    print()
    print("Secrets are masked, so it is safe to paste. Worth a skim before you do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
