#!/usr/bin/env python3
"""Send one press straight at the blaster, with everything else out of the way.

    ./scripts/press.py                    # what the board has, and its signal
    ./scripts/press.py power              # one press
    ./scripts/press.py temp_down temp_down temp_up   # a few, in order

No Pi, no service, no scheduler, no plug. Just this machine, the blaster board
and the unit, which is what makes it worth having: when the app says the blaster
is green and the unit does nothing, this is the command that says which half is
lying.

The device bar can only ever tell you the board answered. Whether the infrared
left the LED, arrived at the unit, and was understood is not something anything
in this project can observe, because infrared is one-way. So the test is to send
one press with the whole stack removed and look at the bed with your own eyes.

What the two outcomes mean:

    the unit reacts     the board and the beam are fine, so the fault is in the
                        service or the Pi, and journalctl is the next stop
    nothing happens     the beam is the problem. Line of sight, aim, distance, or
                        the LED itself. Nothing in software will fix it
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def _with_the_right_python() -> None:
    """Start again under the virtual environment if this one cannot do the job.

    Unlike plug.py, this needs the ESPHome library, which lives in the project's
    virtual environment rather than in the system Python. Asking someone to
    remember that is asking it at the worst possible moment: this is the tool for
    when the bed will not respond and the answer is wanted in the next minute.
    """
    try:
        import aioesphomeapi  # noqa: F401

        return
    except ImportError:
        pass
    for candidate in (ROOT / "backend" / ".venv" / "bin" / "python",
                      Path("/opt/hydrosnooze/backend/.venv/bin/python")):
        if candidate.exists() and Path(sys.executable).resolve() != candidate.resolve():
            os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])
    print(
        "This one needs the ESPHome library, which is in the project's virtual\n"
        "environment. Either run ./scripts/dev.sh once to build it, or:\n"
        "\n"
        "  backend/.venv/bin/python scripts/press.py power",
        file=sys.stderr,
    )
    raise SystemExit(1)


_with_the_right_python()

import logging

# The library narrates its own reconnection attempts at warning level, which on a
# board that is genuinely off the network buries the one line worth reading.
logging.getLogger("aioesphomeapi").setLevel(logging.CRITICAL)

from hydrosnooze.adapters.esphome import EsphomeTransmitter
from hydrosnooze.models import Button

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"

#: Long enough for the unit to act on one press before the next arrives, and the
#: same gap the service uses between presses in a sequence.
GAP_S = 0.35


def env(name: str, fallback: str = "") -> str:
    """Read a setting the way the service does, from .env if it is there."""
    if name in os.environ:
        return os.environ[name]
    for where in (ROOT / "backend" / ".env", Path("/opt/hydrosnooze/backend/.env")):
        if not where.exists():
            continue
        for line in where.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip("\"'")
    return fallback


async def run(host: str, key: str, buttons: list[str], repeat: int) -> int:
    tx = EsphomeTransmitter(host, 6053, key)
    print()
    print(f"{DIM}Blaster at {host}{RESET}")

    try:
        missing = await tx.missing_buttons()
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}Nothing answered.{RESET} {DIM}{str(exc)[:120]}{RESET}", file=sys.stderr)
        print()
        print("So this is a network problem rather than an infrared one, and it is")
        print("the opposite of what the app is showing. Check the board is powered")
        print("and on the Wi-Fi before looking at anything else.")
        return 1

    if missing:
        print(f"{RED}It answered, but it has no code for: {', '.join(missing)}{RESET}")
        print()
        print("Recapture those with ./scripts/capture.py. The board is fine.")
        return 1

    print(f"{GREEN}It answered, and all {len(Button)} buttons have a code.{RESET}")

    if not buttons:
        print()
        print("Nothing sent. Give it a button to press:")
        print()
        print(f"  {BOLD}./scripts/press.py power{RESET}")
        print()
        print(f"{DIM}One of: {', '.join(b.value for b in Button)}{RESET}")
        return 0

    print()
    print(f"{BOLD}Watch the unit.{RESET} Sending, {repeat} time(s) each:")
    print()
    for name in buttons:
        for i in range(repeat):
            try:
                await tx.press(Button(name), "by hand")
            except Exception as exc:  # noqa: BLE001
                print(f"  {RED}{name}: {exc}{RESET}", file=sys.stderr)
                return 1
            note = f" ({i + 1}/{repeat})" if repeat > 1 else ""
            print(f"  {GREEN}sent{RESET}  {name}{note}")
            await asyncio.sleep(GAP_S)

    await tx.close()
    print()
    print("Every one of those left the board. Whether any of them arrived is")
    print("something only the unit can tell you, so:")
    print()
    print(f"  {BOLD}Did the unit react?{RESET}")
    print()
    print(f"  {DIM}yes  the beam is fine. The fault is in the service or the Pi:{RESET}")
    print(f"  {DIM}     journalctl -u hydrosnooze -n 100{RESET}")
    print(f"  {DIM}no   the beam is the problem. Line of sight, aim, distance or{RESET}")
    print(f"  {DIM}     the LED itself, and no software change will help.{RESET}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "buttons",
        nargs="*",
        help=f"buttons to press, in order. One of: {', '.join(b.value for b in Button)}",
    )
    parser.add_argument("--host", help="the blaster, if not the configured one")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="send each one this many times, for testing at the edge of range",
    )
    args = parser.parse_args()

    known = {b.value for b in Button}
    unknown = [b for b in args.buttons if b not in known]
    if unknown:
        print(f"{RED}Not a button: {', '.join(unknown)}{RESET}", file=sys.stderr)
        print(f"Try one of: {', '.join(sorted(known))}", file=sys.stderr)
        return 1

    host = args.host or env("HS_ESPHOME_HOST", "hydrosnooze-ir.local")
    key = env("HS_ESPHOME_ENCRYPTION_KEY")
    return asyncio.run(run(host, key, args.buttons, max(1, args.repeat)))


if __name__ == "__main__":
    raise SystemExit(main())
