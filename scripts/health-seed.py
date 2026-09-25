#!/usr/bin/env python3
"""Four weeks of invented sleep for the seed site, through the real report.

    backend/.venv/bin/python scripts/health-seed.py

The seed site, what Vercel serves and what VITE_SEED_DATA=true builds, has no
service behind it, so the Health Report there needs nights from somewhere. Hand
writing them would give a screen that looks right against data that could never
happen. So this invents twenty-eight nights with the same machinery as the test
fixtures, keeps them in a throwaway database, and asks the real Health Report
builder for every morning. What the mock serves is then exactly what the service
would: the shape, the verdicts, Routine and the vitals learning and then not.

**Invented.** Nobody slept these. The repository is public.

One morning in the four weeks has no night, the way a mat that missed one looks,
so the empty ring is on the seed site too. Writes
frontend/src/api/seed-health.json.

The bed's temperature is invented alongside, the way the probes would have
recorded it: a reading every thirty seconds in local time, easing towards what
each stage asks for, a degree warmer with somebody in it. And the Autopilot
screen's sleep for the last night is worked out by the real against_usual, and
its Sleep timing card by the real timing.timing against the seed site's own
starting schedule, so the mock serves what the service would for those too.

Each night also runs a plan: the seed site's schedule times, with Deep or REM a
degree off on about half the nights, marked as test nights. The bed follows that
plan, what each night ran is written down by the real trials.build_run, and the
Scoreboard is the real scoreboard.scoreboard over them. The mat's nights have
nothing to do with the settings, so the Scoreboard says what it would say about
nights like that: not sure yet.
Four weeks rather than two because the timing card only suggests anything after
fourteen nights on the mornings the schedule runs, and the seed site's schedule
runs on weekdays.

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
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import math  # noqa: E402

from hydrosnooze.db import Database  # noqa: E402
from hydrosnooze import report, trials  # noqa: E402
from hydrosnooze.models import Mode, NightPlan, Schedule, SleepStage, Stage, plan_for_wake  # noqa: E402
from hydrosnooze.withings import health, parse, scoreboard, timing  # noqa: E402

OUT = ROOT / "frontend" / "src" / "api" / "seed-health.json"
LONDON = ZoneInfo("Europe/London")

#: The last morning, and how many before it.
LAST = date(2026, 9, 24)
NIGHTS = 28
#: The morning the mat "missed".
MISSED = date(2026, 9, 16)

SEED = 20260924

#: The scores, oldest first, chosen rather than drawn so the seed site shows
#: every verdict: Good, Fair and one Low. sleep_score is Withings' own number and
#: nothing else is worked out from it, so setting it breaks no rule.
SCORES = (
    80, 74, 85, 68, 90, 77, 82, 71, 86, 79, 63, 84, 88, 76,
    72, 81, 64, 88, 79, None, 55, 84, 91, 77, 86, 69, 83, 87,
)

#: The night the invented bed is asked for, in the order the app runs it: warmer
#: to get into, cooler for deep sleep, a little warmer for REM, warm to wake to.
DRIFT_C, DEEP_C, REM_C, WAKE_C = 29, 25, 26, 28
#: The seed site's schedule (frontend/src/api/mock.ts): its parts, and its wake.
PARTS_MIN = (35, 222, 195, 28)
WAKE_AT = time(6, 30)
#: How long the bed takes to close most of the gap to a new setting, and what a
#: body adds while it is in it.
LAG_S = 20 * 60
BODY_C = 1.0


def plan_for(morning: date, rng: random.Random) -> tuple[NightPlan, str | None, int | None]:
    """The night the bed runs, and which part was a test, if one was.

    About a third of nights move Deep a degree and a fifth move REM, one part at
    a time, the way the evening suggestions will.
    """
    temps = {"drift": DRIFT_C, "deep": DEEP_C, "rem": REM_C, "wake": WAKE_C}
    test, offset = None, None
    roll = rng.random()
    if roll < 0.35:
        test, offset = "deep", rng.choice((-1, 1))
    elif roll < 0.55:
        test, offset = "rem", rng.choice((-1, 1))
    if test:
        temps[test] += offset
    stages = [
        SleepStage(stage, minutes, temps[stage.value])
        for stage, minutes in zip((Stage.DRIFT, Stage.DEEP, Stage.REM, Stage.WAKE), PARTS_MIN)
    ]
    return plan_for_wake(morning, WAKE_AT, stages, Mode.QUIET), test, offset


def asked_for(t: float, plan: NightPlan) -> int:
    """What the bed is set to at `t`: the part of the plan it falls in, the first
    part before lights out, the last after the wake time."""
    at = datetime.fromtimestamp(t, LONDON).replace(tzinfo=None)
    for step in plan.steps:
        if step.starts_at <= at < step.ends_at:
            return step.temp_c
    return plan.steps[0].temp_c if at < plan.bedtime_at else plan.steps[-1].temp_c


def invent_bed(db: Database, night: parse.Night, rng: random.Random, plan: NightPlan) -> None:
    """Thirty-second probe readings for one night, written as the Pi writes them."""
    in_bed = {m.at for m in night.minutes}
    lean = rng.uniform(-0.4, 0.4)
    bed, room = 21.0, rng.uniform(18.5, 20.5)
    first = int(
        (plan.bedtime_at - timedelta(hours=1)).replace(tzinfo=LONDON).timestamp()
    )
    t, i = min(night.start_at - 3600, first), 0
    while t < night.end_at + 600:
        target = asked_for(t, plan)
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


def shaped_like_a_night(rng: random.Random, minutes: int) -> list[int]:
    """A night a minute at a time, shaped the way real nights are.

    Used in place of the fixtures' own shape, which keeps deep sleep going in
    every cycle to the morning. That is fine for testing a parser and wrong for
    the Sleep timing card, which is about exactly where deep sleep falls. Real
    nights run in cycles of about ninety minutes, with most of the deep sleep in
    the first two and the REM growing towards the morning. The test fixtures are
    left as they are.
    """
    awake, light, deep_, rem_ = parse.AWAKE, parse.LIGHT, parse.DEEP, parse.REM
    out = [awake] * rng.randint(10, 35)
    cycle = 0
    while len(out) < minutes:
        deep = (
            (rng.randint(30, 50), rng.randint(20, 35), rng.randint(5, 15))[cycle]
            if cycle < 3
            else rng.randint(0, 6)
        )
        rem = max(3, min(10 + 8 * cycle, 40) + rng.randint(-5, 5))
        rest = max(10, rng.randint(80, 105) - deep - rem)
        out += [light] * (rest // 2) + [deep_] * deep + [light] * (rest - rest // 2) + [rem_] * rem
        if rng.random() < 0.5:
            out += [awake] * rng.randint(1, 6)
        cycle += 1
    return out[:minutes]


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
    fx.stages = shaped_like_a_night
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
        plan, test, offset = plan_for(morning, rng)
        invent_bed(db, night, rng, plan)
        start, end = report.window(plan)
        run = trials.build_run(plan, db.night_history(start, end), [])
        db.save_night_run(
            replace(run, test_part=test, test_offset_c=offset),
            datetime.combine(morning, time(7, 0)),
        )

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
        # The Sleep timing card, against the schedule the seed site starts with
        # (frontend/src/api/mock.ts): weekdays, 22:30 to 06:30, the default parts.
        "timing": timing.timing(db, Schedule(days_of_week=[0, 1, 2, 3, 4]), LAST),
        # The Scoreboard, over what each night ran and what the mat measured.
        "scoreboard": scoreboard.scoreboard(db, LAST),
    }
    OUT.write_text(json.dumps(seed, separators=(",", ":")) + "\n")
    db.close()
    held = sum(1 for r in reports.values() if r and r["night"])
    print(f"{held} invented nights, {len(reports)} mornings, written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
