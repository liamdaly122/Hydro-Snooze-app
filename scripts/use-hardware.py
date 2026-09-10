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
DEFAULT_PROBES_HOST = "hydrosnooze-temp.local"

#: Everything this script owns. Any of these already in the file get replaced,
#: so running it twice does not leave two of anything.
OWNED = (
    "HS_TRANSMITTER",
    "HS_ESPHOME_HOST",
    "HS_ESPHOME_ENCRYPTION_KEY",
    "HS_POWER_MONITOR",
    "HS_SHELLY_HOST",
    "HS_PROBES_HOST",
    "HS_PROBES_ENCRYPTION_KEY",
)


def read_key(name: str, required: bool = True) -> str:
    """One key out of docs/secrets.yaml, never printed."""
    if not SECRETS.exists():
        if not required:
            return ""
        die(
            f"{SECRETS.relative_to(ROOT)} is missing.",
            "Copy docs/secrets.yaml.example to docs/secrets.yaml and fill it in.",
        )
    match = re.search(rf"^\s*{name}\s*:\s*[\"']?([^\"'\s]+)", SECRETS.read_text(), re.M)
    if match is None:
        if not required:
            return ""
        die(f"No {name} in docs/secrets.yaml.")
    return match.group(1)


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
    parser.add_argument("--probes-host", default=DEFAULT_PROBES_HOST)
    parser.add_argument(
        "--env",
        default=None,
        help="which .env to write. On the Pi that is /opt/hydrosnooze/.env, "
        "since the service runs from there rather than from the clone.",
    )
    args = parser.parse_args()

    # On the Pi the service runs from /opt/hydrosnooze, so the file to edit is
    # not the one inside the clone. Shown as an absolute path when it is outside
    # the project, because a bare ".env" there would be genuinely ambiguous.
    env = Path(args.env).expanduser() if args.env else ENV
    shown = env if args.env else env.relative_to(ROOT)

    if not env.exists():
        if not EXAMPLE.exists():
            die(f"Neither {shown} nor backend/.env.example exists.")
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(EXAMPLE.read_text())
        print(f"Created {shown} from the example.")

    body = strip_owned(env.read_text())

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
        # Optional, and quietly skipped when there is no probe board yet, so
        # this script keeps working on a setup that predates them.
        probe_key = read_key("hydrosnooze_temp_api_key", required=False)
        if probe_key:
            block += [
                f"HS_PROBES_HOST={args.probes_host}",
                f"HS_PROBES_ENCRYPTION_KEY={probe_key}",
            ]
        masked = key[:4] + "..." + key[-4:] if len(key) > 12 else "set"
        summary = [
            "Now driving the REAL HydroSnooze.",
            f"  blaster   {args.esphome_host}   key {masked}",
            f"  plug      {args.shelly_host}",
        ] + (
            [f"  probes    {args.probes_host}   key {probe_key[:4]}...{probe_key[-4:]}"]
            if probe_key
            else ["  probes    none set up yet"]
        ) + [
            "",
            "Presses will land on the actual unit. Nothing is simulated any more.",
        ]

    env.write_text(body + "\n" + "\n".join(block) + "\n")
    print()
    print(f"Wrote {shown}")

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
