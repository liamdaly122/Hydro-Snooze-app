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
        db.save_sleep_night(parse.night(summary, body["series"]))

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
    }
    OUT.write_text(json.dumps(seed, separators=(",", ":")) + "\n")
    db.close()
    held = sum(1 for r in reports.values() if r and r["night"])
    print(f"{held} invented nights, {len(reports)} mornings, written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
