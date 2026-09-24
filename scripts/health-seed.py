#!/usr/bin/env python3
"""A fortnight of invented sleep for the seed site, through the real report.

    backend/.venv/bin/python scripts/health-seed.py

The seed site, what Vercel serves and what VITE_SEED_DATA=true builds, has no
service behind it, so the Health Report there needs nights from somewhere. Hand
writing them would give a screen that looks right against data that could never
happen. So this invents fourteen nights with the same machinery as the test
fixtures, keeps them in a throwaway database, and asks the real Health Report
builder for every morning. What the mock serves is then exactly what the service
would: the shape, the verdicts, Routine and the vitals learning and then not.

**Invented.** Nobody slept these. The repository is public.

One morning in the fortnight has no night, the way a mat that missed one looks,
so the empty ring is on the seed site too. Writes
frontend/src/api/seed-health.json.

The bed's temperature is invented alongside, the way the probes would have
recorded it: a reading every thirty seconds in local time, easing towards what
each stage asks for, a degree warmer with somebody in it. And the Autopilot
screen's sleep for the last night is worked out by the real against_usual, so
the mock serves what the service would for that too.

    backend/.venv/bin/python scripts/health-seed.py --db /tmp/sleep.db

keeps the nights in that database as well, so the real service can be run
against them with HS_DB_PATH=/tmp/sleep.db and the screen seen with no mat. Never
point it at the Pi's database: invented nights would sit among the real ones.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import math  # noqa: E402

from hydrosnooze.db import Database  # noqa: E402
from hydrosnooze.withings import health, parse  # noqa: E402

OUT = ROOT / "frontend" / "src" / "api" / "seed-health.json"
LONDON = ZoneInfo("Europe/London")

#: The last morning, and how many before it.
LAST = date(2026, 9, 24)
NIGHTS = 14
#: The morning the mat "missed".
MISSED = date(2026, 9, 16)

SEED = 20260924

#: The scores, oldest first, chosen rather than drawn so the seed site shows
#: every verdict: Good, Fair and one Low. sleep_score is Withings' own number and
#: nothing else is worked out from it, so setting it breaks no rule.
SCORES = (72, 81, 64, 88, 79, None, 55, 84, 91, 77, 86, 69, 83, 87)

#: The night the invented bed is asked for, in the order the app runs it: warmer
#: to get into, cooler for deep sleep, a little warmer for REM, warm to wake to.
DRIFT_C, DEEP_C, REM_C, WAKE_C = 29, 25, 26, 28
#: How long the bed takes to close most of the gap to a new setting, and what a
#: body adds while it is in it.
LAG_S = 20 * 60
BODY_C = 1.0


def asked_for(t: float, start: int, end: int) -> int:
    """What the bed is set to at `t`, for a night in bed from `start` to `end`."""
    if t < start + 30 * 60:
        return DRIFT_C
    if t < start + 4 * 3600:
        return DEEP_C
    if t < end - 60 * 60:
        return REM_C
    return WAKE_C


def invent_bed(db: Database, night: parse.Night, rng: random.Random) -> None:
    """Thirty-second probe readings for one night, written as the Pi writes them."""
    in_bed = {m.at for m in night.minutes}
    lean = rng.uniform(-0.4, 0.4)
    bed, room = 21.0, rng.uniform(18.5, 20.5)
    t, i = night.start_at - 3600, 0
    while t < night.end_at + 600:
        target = asked_for(t, night.start_at, night.end_at)
        minute = night.start_at + ((t - night.start_at) // 60) * 60
        body = BODY_C if minute in in_bed else 0.0
        settle = target + body + lean
        bed += (settle - bed) * (1 - math.exp(-30 / LAG_S))
        reading = round(bed + rng.gauss(0, 0.08), 2)
        at = datetime.fromtimestamp(t, LONDON).replace(tzinfo=None) + timedelta(
            microseconds=(i * 7919) % 1_000_000
        )
        db.add_power_sample(
            at, 168.0, flow_c=round(target - 0.4, 2), return_c=reading, room_c=round(room, 2),
            target_c=target,
        )
        t += 30
        i += 1
    db.flush_power()


def fixtures():
    """scripts/withings-fixtures.py, which has a hyphen in its name."""
    spec = importlib.util.spec_from_file_location(
        "withings_fixtures", ROOT / "scripts" / "withings-fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["withings_fixtures"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="also keep the nights in this database")
    args = parser.parse_args()
    if args.db and Path(args.db).exists():
        sys.exit(f"{args.db} already exists. This only ever writes a new one.")

    fx = fixtures()
    rng = random.Random(SEED)
    db = Database(Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "seed.db")
    mornings = [LAST - timedelta(days=i) for i in range(NIGHTS - 1, -1, -1)]

    for n, morning in enumerate(mornings, 1):
        if morning == MISSED:
            continue
        evening = morning - timedelta(days=1)
        bed = fx.local(evening.year, evening.month, evening.day, 22, 10) + timedelta(
            minutes=rng.randint(0, 70)
        )
        up = fx.local(morning.year, morning.month, morning.day, 6, 45) + timedelta(
            minutes=rng.randint(0, 60)
        )
        trips = ()
        if rng.random() < 0.3:
            out = fx.local(morning.year, morning.month, morning.day, 3, 0) + timedelta(
                minutes=rng.randint(0, 90)
            )
            trips = ((out, out + timedelta(minutes=rng.randint(3, 9))),)
        plan = fx.Plan("seed", bed, up, trips=trips, ends_asleep=rng.random() < 0.2)
        summary, body = fx.invent(plan, rng, 100 + n)
        summary["data"]["sleep_score"] = SCORES[n - 1]
        broken = fx.rules(summary, body)
        if broken:
            sys.exit(f"{summary['date']} breaks: {'; '.join(broken)}")
        night = parse.night(summary, body["series"])
        db.save_sleep_night(night)
        invent_bed(db, night, rng)

    reports = {m.isoformat(): health.report(db, m.isoformat()) for m in mornings}
    latest = LAST.isoformat()
    seed = {
        "_note": "Invented by scripts/health-seed.py. Nobody slept these nights.",
        "earliest": mornings[0].isoformat(),
        "latest": latest,
        "status": {
            "configured": True,
            "connected": True,
            "needs_reconnect": False,
            "waiting_for_clock": False,
            "last_sync_at": datetime(2026, 9, 24, 7, 52, tzinfo=LONDON).isoformat(),
            "last_error": None,
            "latest_night": latest,
        },
        "reports": reports,
        # The Autopilot screen's sleep for the last night, as the service's
        # autopilot_json would give it.
        "autopilot_sleep": [
            {
                "key": a.key,
                "label": a.label,
                "seconds": a.seconds,
                "usual_seconds": a.usual_seconds,
                "nights": a.nights,
                "change_pct": a.change_pct,
                "better": a.better,
            }
            for a in health.against_usual(db, latest)
        ],
    }
    OUT.write_text(json.dumps(seed, separators=(",", ":")) + "\n")
    db.close()
    held = sum(1 for r in reports.values() if r and r["night"])
    print(f"{held} invented nights, {len(reports)} mornings, written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
