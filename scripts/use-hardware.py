#!/usr/bin/env python3
"""Point the service at the real blaster and plug, or back at the simulator.

    ./scripts/use-hardware.py           # drive the real HydroSnooze
    ./scripts/use-hardware.py --fake    # back to the simulated one

This edits backend/.env, which is the one file that decides whether the service
talks to hardware or to a simulation. Doing it by hand means opening a hidden
file in a terminal editor and pasting a 44 character key without a typo, which is
a poor way to spend an evening.

The ESPHome key is copied straight out of docs/secrets.yaml. It is never printed
here and never needs to be seen: both files are gitignored and stay that way.

Safe to run as many times as you like. It rewrites its own lines and leaves
everything else in the file alone.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
EXAMPLE = ROOT / "backend" / ".env.example"
SECRETS = ROOT / "docs" / "secrets.yaml"

#: Worked out during setup and recorded in SETUP.md. Override with the flags if
#: the router ever hands out something different.
DEFAULT_ESPHOME_HOST = "192.168.1.178"
DEFAULT_SHELLY_HOST = "192.168.1.194"

#: Everything this script owns. Any of these already in the file get replaced,
#: so running it twice does not leave two of anything.
OWNED = (
    "HS_TRANSMITTER",
    "HS_ESPHOME_HOST",
    "HS_ESPHOME_ENCRYPTION_KEY",
    "HS_POWER_MONITOR",
    "HS_SHELLY_HOST",
)


def read_api_key() -> str:
    if not SECRETS.exists():
        die(
            f"{SECRETS.relative_to(ROOT)} is missing.",
            "Copy docs/secrets.yaml.example to docs/secrets.yaml and fill it in.",
        )
    match = re.search(
        r"^\s*hydrosnooze_api_key\s*:\s*[\"']?([^\"'\s]+)", SECRETS.read_text(), re.M
    )
    if match is None:
        die(
            "No hydrosnooze_api_key in docs/secrets.yaml.",
            "It is the same key as the api: encryption: block in the ESPHome config.",
        )
    return match.group(1)


def die(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)
    raise SystemExit(1)


def strip_owned(text: str) -> str:
    """Drop the lines this script manages, commented or not."""
    kept = [
        line
        for line in text.splitlines()
        if not any(re.match(rf"^\s*#?\s*{name}\s*=", line) for name in OWNED)
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="go back to the simulator")
    parser.add_argument("--esphome-host", default=DEFAULT_ESPHOME_HOST)
    parser.add_argument("--shelly-host", default=DEFAULT_SHELLY_HOST)
    args = parser.parse_args()

    if not ENV.exists():
        if not EXAMPLE.exists():
            die("Neither backend/.env nor backend/.env.example exists.")
        ENV.write_text(EXAMPLE.read_text())
        print(f"Created {ENV.relative_to(ROOT)} from the example.")

    body = strip_owned(ENV.read_text())

    if args.fake:
        block = [
            "",
            "# --- Simulated, set by scripts/use-hardware.py --fake ---",
            "HS_TRANSMITTER=fake",
            "HS_POWER_MONITOR=fake",
        ]
        summary = [
            "Now driving the SIMULATED unit.",
            "Every press is printed in the terminal instead of being sent.",
        ]
    else:
        key = read_api_key()
        block = [
            "",
            "# --- Real hardware, set by scripts/use-hardware.py ---",
            "HS_TRANSMITTER=esphome",
            f"HS_ESPHOME_HOST={args.esphome_host}",
            f"HS_ESPHOME_ENCRYPTION_KEY={key}",
            "HS_POWER_MONITOR=shelly",
            f"HS_SHELLY_HOST={args.shelly_host}",
        ]
        masked = key[:4] + "..." + key[-4:] if len(key) > 12 else "set"
        summary = [
            "Now driving the REAL HydroSnooze.",
            f"  blaster   {args.esphome_host}   key {masked}",
            f"  plug      {args.shelly_host}",
            "",
            "Presses will land on the actual unit. Nothing is simulated any more.",
        ]

    ENV.write_text(body + "\n" + "\n".join(block) + "\n")

    print()
    for line in summary:
        print(line)

    if not args.fake:
        venv = ROOT / "backend" / ".venv" / "bin" / "python"
        if venv.exists():
            import subprocess

            missing = subprocess.run(
                [str(venv), "-c", "import aioesphomeapi"], capture_output=True
            ).returncode
            if missing:
                print()
                print("aioesphomeapi is not installed yet, which the blaster needs.")
                print("./scripts/dev.sh installs it. Run that and it sorts itself out.")

    print()
    print("Restart the service for this to take effect: Ctrl-C, then ./scripts/dev.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
