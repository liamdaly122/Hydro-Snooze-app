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

#: Set before handing over to the virtual environment's python, so a second
#: failure there is reported rather than becoming an endless loop of exec.
TRIED_ALREADY = "HS_PRESS_SWITCHED_PYTHON"


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

    # A sentinel rather than comparing interpreter paths. The obvious guard, "am
    # I already the venv's python", compares sys.executable against the candidate
    # with both resolved, and a venv's bin/python is a symlink chain ending at the
    # base interpreter. On a machine where the venv was built from the same
    # python that runs this script, those two resolve to the same file, the guard
    # decides it has already arrived, and it never switches at all. Which is
    # exactly what it did on Liam's Mac while telling him to do by hand the thing
    # it was there to do for him.
    if os.environ.get(TRIED_ALREADY):
        print(
            "Ran under the virtual environment and the ESPHome library still is\n"
            "not there. Install it with:\n"
            "\n"
            "  backend/.venv/bin/pip install 'aioesphomeapi>=24.6'",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for candidate in (
        ROOT / "backend" / ".venv" / "bin" / "python",
        Path("/opt/hydrosnooze/backend/.venv/bin/python"),
    ):
        if candidate.exists():
            os.environ[TRIED_ALREADY] = "1"
            os.execv(
                str(candidate),
                [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]],
            )

    print(
        "This one needs the ESPHome library, which lives in the project's virtual\n"
        "environment, and there is no virtual environment here. Build it with:\n"
        "\n"
        "  ./scripts/dev.sh",
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


#: What the blaster's key is called in each of the two places it can live.
KEY_IN_ENV = "HS_ESPHOME_ENCRYPTION_KEY"
KEY_IN_SECRETS = "hydrosnooze_api_key"


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


def from_secrets() -> str:
    """The key out of docs/secrets.yaml, which is what flashed the board.

    Worth checking as well as .env, and on a laptop it is the more reliable of
    the two. The service needs the key in .env because that is what the service
    reads; this machine may never have run the service, but it did flash the
    board, and the board's key came from here.
    """
    where = ROOT / "docs" / "secrets.yaml"
    if not where.exists():
        return ""
    for line in where.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{KEY_IN_SECRETS}:") and not stripped.startswith("#"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def find_key(explicit: str | None) -> tuple[str, str]:
    """The blaster's key and where it was found. Never the key itself, printed."""
    if explicit:
        return explicit, "--key"
    if found := env(KEY_IN_ENV):
        return found, "backend/.env"
    if found := from_secrets():
        return found, "docs/secrets.yaml"
    return "", ""


def looks_like_a_key_problem(exc: Exception) -> bool:
    words = str(exc).lower()
    return "encryption" in words or "invalid key" in words or "psk" in words


async def run(host: str, key: str, source: str, buttons: list[str], repeat: int) -> int:
    tx = EsphomeTransmitter(host, 6053, key)
    print()
    print(f"{DIM}Blaster at {host}{RESET}")
    # The source, never the key. This output gets pasted around.
    print(f"{DIM}Key from {source or 'nowhere: none found'}{RESET}")

    try:
        missing = await tx.missing_buttons()
    except Exception as exc:  # noqa: BLE001
        if looks_like_a_key_problem(exc):
            # The board answered. It just would not talk without the right key,
            # which says nothing at all about the unit or the infrared.
            print(f"{RED}It answered, but would not talk without the right key.{RESET}")
            print()
            if not source:
                print("No key was found. It lives in one of two places:")
                print()
                print(f"  {BOLD}backend/.env{RESET}          as {KEY_IN_ENV}")
                print(f"  {BOLD}docs/secrets.yaml{RESET}     as {KEY_IN_SECRETS}")
                print()
                print("Both are gitignored, so a machine that has never run the")
                print("service or flashed the board will not have either.")
            else:
                print(f"The key in {BOLD}{source}{RESET} is not the one the board was")
                print("flashed with. Check it against the other place it lives, or")
                print("pass the right one with --key.")
            print()
            print(f"{DIM}Either way the board is powered and on the network, which is{RESET}")
            print(f"{DIM}more than this was able to tell you a minute ago.{RESET}")
            return 1

        print(f"{RED}Nothing answered.{RESET} {DIM}{str(exc)[:110]}{RESET}", file=sys.stderr)
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
    parser.add_argument("--key", help="its API key, if it is not in either usual place")
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
    key, source = find_key(args.key)
    return asyncio.run(run(host, key, source, args.buttons, max(1, args.repeat)))


if __name__ == "__main__":
    raise SystemExit(main())
