#!/usr/bin/env python3
"""Read the Shelly plug, live, and say what the service would make of it.

    ./scripts/plug.py 192.168.1.42
    ./scripts/plug.py                    # uses HS_SHELLY_HOST, or the .local name

Nothing else is needed: no Pi, no blaster, no virtual environment. It talks to
the plug over the local network the same way the service does, so if this works
the service's plug half works.

Use it to fill in the four power readings the setup asks for. Put the unit into
each state with the physical remote, wait for the number to settle, and write the
settled column down:

    off at the wall     expect under 5 W
    on, at temperature  expect 5 to 60 W
    actively cooling    expect around 170 W
    actively heating    expect around 300 W

Those four numbers are what separate "the unit is working" from "the unit is
sitting there doing nothing", which is the only thing in this project that is
measured rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# The same classifier the service uses, so this cannot drift from it.
from hydrosnooze.models import PowerThresholds

#: How many readings the settled column averages over. Fifteen seconds is long
#: enough to ride out the compressor's own cycling and short enough to notice a
#: state change while you are still standing next to the unit.
WINDOW = 15


def read_watts(host: str, switch_id: int, timeout: float) -> float | None:
    """The instantaneous draw, or None if the plug could not be reached.

    None is not zero. Zero means the unit is off, None means we do not know, and
    the whole project is built on not confusing the two.
    """
    url = f"http://{host}/rpc/Switch.GetStatus?id={switch_id}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    watts = payload.get("apower")
    return None if watts is None else float(watts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "host",
        nargs="?",
        default=os.environ.get("HS_SHELLY_HOST", "hydrosnooze-plug.local"),
        help="the plug's IP address, or its .local name",
    )
    parser.add_argument("--seconds", type=int, default=0, help="stop after this long")
    parser.add_argument("--switch-id", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()

    thresholds = PowerThresholds()
    print(f"\nReading {args.host} once a second. Ctrl-C to stop.\n")
    print("   time      watts   settled   the service would call this")

    window: list[float] = []
    everything: list[float] = []
    misses = 0
    started = time.monotonic()

    try:
        while True:
            watts = read_watts(args.host, args.switch_id, args.timeout)
            now = time.strftime("%H:%M:%S")

            if watts is None:
                misses += 1
                print(f"  {now}          -         -   unreachable")
            else:
                window.append(watts)
                everything.append(watts)
                del window[:-WINDOW]
                settled = statistics.median(window)
                activity = thresholds.classify(settled)
                print(f"  {now}   {watts:8.1f}  {settled:8.1f}   {activity.value}")

            if args.seconds and time.monotonic() - started >= args.seconds:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    print()
    if not everything:
        print(f"Never got a reading from {args.host}.")
        print("Check the plug is on the same Wi-Fi, and try its IP address rather than the name.")
        return 1

    settled = statistics.median(everything[-WINDOW:])
    print(f"Settled at {settled:.1f} W, which the service reads as "
          f"{thresholds.classify(settled).value}.")
    print(f"Over the whole run: low {min(everything):.1f} W, high {max(everything):.1f} W, "
          f"{len(everything)} readings.")
    if misses:
        print(f"{misses} of them did not come back. A few is normal, a lot means the Wi-Fi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
