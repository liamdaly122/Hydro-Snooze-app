#!/usr/bin/env python3
"""How far the bed ends up from what the unit was asked for.

    ./scripts/calibration.py                 # on the Pi
    ./scripts/calibration.py --db /path/to/hydrosnooze.db

Liam noticed the probes reading about two degrees below whatever the app set,
and asked whether the app should just add two. Maybe. This is the command that
says what the number really is, because guessing at a calibration is how you end
up with a confident app and a cold bed.

It reads finished pre-conditioning runs, which are the only moments this system
measures the bed with **nobody in it**. Once somebody is in the bed, body heat is
the biggest term in the sum and the water is chasing it rather than setting it,
so a night's readings cannot separate the hose loss from the person.

What to look for:

    a steady offset in one mode      worth correcting for, and this says by how
    an offset that grows with the
      distance from the room         it is heat leaking out of the hose run, not
                                     the unit being wrong, and a flat correction
                                     will be wrong at one end of the range
    opposite signs in the two modes  expected. Water warms on its way to the bed
                                     when cooling and cools when warming, so a
                                     fix that only looks at warming nights will
                                     make cooling nights worse
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/opt/hydrosnooze/data/hydrosnooze.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.environ.get("HS_DB_PATH", DEFAULT_DB))
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"No database at {path}.", file=sys.stderr)
        print("Run this on the Pi, or pass --db with the path to a copy.", file=sys.stderr)
        return 1

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT at, mode, target_c, end_c, room_c, decided_by FROM precondition_runs "
        "WHERE reached = 1 AND end_c IS NOT NULL ORDER BY at"
    ).fetchall()

    if not rows:
        print("No finished runs with probe readings yet.")
        print()
        print("This needs runs where the probes decided the bed had arrived, which")
        print("means the probe board has to have been up during pre-conditioning.")
        print("A few nights will do it.")
        return 0

    print(f"{len(rows)} finished runs, measured on the hoses with nobody in the bed.")
    print()
    print(f"  {'when':16} {'mode':8} {'asked':>6} {'bed':>7} {'off by':>7} {'room':>6}")
    print(f"  {'-' * 16} {'-' * 8} {'-' * 6} {'-' * 7} {'-' * 7} {'-' * 6}")

    by_mode: dict[str, list[float]] = {}
    for r in rows:
        off = r["end_c"] - r["target_c"]
        by_mode.setdefault(r["mode"], []).append(off)
        room = f"{r['room_c']:.1f}" if r["room_c"] is not None else "  --"
        print(
            f"  {r['at'][:16]:16} {r['mode']:8} {r['target_c']:6} "
            f"{r['end_c']:7.1f} {off:+7.1f} {room:>6}"
        )

    print()
    print("  Average, per mode:")
    for mode, offs in sorted(by_mode.items()):
        mean = sum(offs) / len(offs)
        spread = max(offs) - min(offs)
        note = "" if len(offs) >= 3 else "   (one or two runs is an anecdote)"
        print(f"    {mode:9} {mean:+.1f}C over {len(offs)} runs, spread {spread:.1f}C{note}")

    print()
    print("  A negative number means the bed ends up cooler than the app asked for.")
    print("  If the two modes disagree in sign, that is the hose run losing heat")
    print("  both ways rather than the unit being wrong, and one flat correction")
    print("  will not fix both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
