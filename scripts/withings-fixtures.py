#!/usr/bin/env python3
"""Invent nights on the Sleep Analyzer, shaped exactly like real ones.

    ./scripts/withings-fixtures.py                  # write the fixtures
    ./scripts/withings-fixtures.py --check FOLDER   # hold a real capture to the same rules

The parser gets tested against Withings responses, and this repository is public,
so the responses it is tested against are made up. Not loosely. Every rule that
seven real nights on my mat obeyed, these obey too, and this refuses to write a
night that breaks one. docs/withings.md has the rules and how they were found.

What is real is the shape: every key and every type, the JSON inside a string,
the timestamps as text, the hash on every interval, `model` meaning two things,
the gaps where nobody was in bed, the intervals cut shorter than the stages, and
the zeros that mean no reading. What is invented is every value: the dates, the
times, the heart rate, the breathing and the sleep.

Four nights, each there for a reason:

    2026-10-22  plain. No trips out of bed, awake a while before getting up
    2026-10-23  a trip at 03:40, so the night exists hours before the morning
    2026-10-24  two trips, the second 35 minutes long, back into bed after the
                first already asleep, and still asleep at the very end, so
                wakeup_latency is 0
    2026-10-25  the night the clocks go back. It runs through 01:00 to 02:00
                twice, with a trip in the second one

Seeded, so it writes the same bytes every time. Writes to
backend/tests/fixtures/withings/.

--check runs the same rules over a folder from scripts/withings-capture.py, so a
rule that stops holding on a real night gets noticed rather than assumed.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "tests" / "fixtures" / "withings"
LONDON = ZoneInfo("Europe/London")

SEED = 20261022

AWAKE, LIGHT, DEEP, REM = 0, 1, 2, 3
SLEEPING = (LIGHT, DEEP, REM)

#: Plainly not a device anybody owns: the hash of a sentence saying so.
DEVICE = hashlib.sha1(b"HydroSnooze test fixture, not a real device").hexdigest()

#: A number on the summary and the get body, a name on each interval.
MODEL, MODEL_NAME, MODEL_ID = 32, "Aura Sensor V2", 63

METRICS = ("hr", "rr", "snoring", "sdnn_1", "rmssd", "mvt_score", "hrv_quality",
           "chest_movement_rate")

#: How the real nights cut time in bed into intervals: whole minutes, one to ten
#: of them, mostly short. The median was two. A stage is a run of these.
CHUNK_MINUTES = (1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 5, 6, 8, 10)

GREEN, RED, BOLD, RESET = "\033[32m", "\033[31m", "\033[1m", "\033[0m"


def local(y: int, mo: int, d: int, h: int, mi: int, fold: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=LONDON, fold=fold)


@dataclass(frozen=True)
class Plan:
    why: str
    into_bed: datetime
    out_of_bed: datetime
    #: Each time out of bed, as (got up, got back in).
    trips: tuple[tuple[datetime, datetime], ...] = ()
    #: Which trips end straight back in light sleep, by position. Real nights do
    #: this: the first interval after a gap can already be asleep.
    asleep_on_return: tuple[int, ...] = ()
    #: Asleep in the last interval, so the final "woke up" is the last "out of
    #: bed" and wakeup_latency is 0.
    ends_asleep: bool = False


PLANS = (
    Plan("plain", local(2026, 10, 21, 22, 40), local(2026, 10, 22, 7, 10)),
    Plan(
        "a trip at 03:40",
        local(2026, 10, 22, 22, 25),
        local(2026, 10, 23, 7, 30),
        trips=((local(2026, 10, 23, 3, 40), local(2026, 10, 23, 3, 46)),),
    ),
    Plan(
        "two trips, back asleep, ends asleep",
        local(2026, 10, 23, 23, 5),
        local(2026, 10, 24, 8, 20),
        trips=(
            (local(2026, 10, 24, 2, 10), local(2026, 10, 24, 2, 14)),
            (local(2026, 10, 24, 6, 5), local(2026, 10, 24, 6, 40)),
        ),
        asleep_on_return=(0,),
        ends_asleep=True,
    ),
    Plan(
        "the clocks go back",
        local(2026, 10, 24, 22, 50),
        local(2026, 10, 25, 7, 5),
        # fold=1 is the second 01:20, in GMT, after the clocks have gone back.
        trips=((local(2026, 10, 25, 1, 20, fold=1), local(2026, 10, 25, 1, 32, fold=1)),),
    ),
)


# --- Inventing a night ---------------------------------------------------------


def half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paint(states: list[int], a: int, b: int, value: int) -> None:
    for m in range(max(a, 0), min(b, len(states))):
        states[m] = value


def stages(rng: random.Random, minutes: int) -> list[int]:
    """A night of made-up sleep, a minute at a time, in the usual order: a while
    awake, then cycles of light, deep, light and REM, deep early and REM late,
    with short wakings between."""
    out = [AWAKE] * rng.randint(15, 40)
    cycle = 0
    while len(out) < minutes:
        late = min(cycle / 5, 1.0)
        out += [LIGHT] * rng.randint(10, 30)
        out += [DEEP] * max(3, int(rng.randint(15, 45) * (1 - 0.7 * late)))
        out += [LIGHT] * rng.randint(8, 25)
        out += [REM] * max(2, int(rng.randint(4, 12) * (1 + 1.5 * late)))
        if rng.random() < 0.6:
            out += [AWAKE] * rng.randint(2, 12)
        cycle += 1
    return out[:minutes]


def deltas(times: list[int], start: int) -> list[int]:
    """Gaps, not times: the first from startdate, the rest from the one before."""
    out, last = [], start
    for t in times:
        out.append(t - last)
        last = t
    return out


def events(intervals: list[dict]) -> dict[str, list[int]]:
    """The four kinds of event, as absolute times, worked out from the intervals
    the same way the real nights turned out to have them."""
    into, asleep, woke, out = [], [], [], []
    prev = None
    for e in intervals:
        if prev is None or prev["enddate"] != e["startdate"]:
            if prev is not None:
                if prev["state"] != AWAKE:
                    woke.append(prev["enddate"])
                out.append(prev["enddate"])
            into.append(e["startdate"])
            if e["state"] != AWAKE:
                asleep.append(e["startdate"])
        elif prev["state"] == AWAKE and e["state"] != AWAKE:
            asleep.append(e["startdate"])
        elif prev["state"] != AWAKE and e["state"] == AWAKE:
            woke.append(e["startdate"])
        prev = e
    if prev is not None:
        if prev["state"] != AWAKE:
            woke.append(prev["enddate"])
        out.append(prev["enddate"])
    return {"1": into, "2": asleep, "3": woke, "4": out}


def vitals(rng: random.Random, state: int) -> dict[str, int]:
    """One minute of invented readings, with the real nights' quirks in them."""
    hr = {AWAKE: 63, LIGHT: 58, DEEP: 54, REM: 60}[state] + rng.gauss(0, 2.5)
    rr = {AWAKE: 13, LIGHT: 12, DEEP: 11, REM: 13}[state] + rng.gauss(0, 1.2)
    if state == AWAKE and rng.random() < 0.08:
        hr += rng.randint(8, 25)
    if state == AWAKE and rng.random() < 0.05:
        rr += rng.randint(4, 10)
    hr_i = max(44, min(95, round(hr)))
    rr_i = max(8, min(26, round(rr)))

    sdnn = rng.randint(20, 250) if state == AWAKE else rng.randint(35, 140)
    rmssd = max(5, min(93, int(sdnn * rng.uniform(0.3, 0.6))))
    quality = max(30, min(99, round(rng.gauss(95, 5))))
    # No variability reading: both zero together, nearly always while awake.
    if rng.random() < (0.09 if state == AWAKE else 0.001):
        sdnn = rmssd = 0
        quality = 0 if rng.random() < 0.4 else rng.choice((7, 14, 47, 60, 75))
    elif rng.random() < 0.0015:
        rmssd = 0
    elif rng.random() < 0.001:
        quality = 0

    if state == AWAKE:
        # 255 is the top of the scale, and the real nights hit it a few times.
        roll = rng.random()
        if roll < 0.006:
            mvt = 255
        elif roll < 0.046:
            mvt = rng.randint(150, 245)
        else:
            mvt = rng.randint(3, 120)
    else:
        mvt = rng.randint(13, 60) if rng.random() < 0.05 else rng.randint(1, 12)

    return {
        "hr": hr_i,
        "rr": rr_i,
        "snoring": 0,
        "sdnn_1": sdnn,
        "rmssd": rmssd,
        "mvt_score": mvt,
        "hrv_quality": quality,
        # Not a second measurement. On every real night it was rr, value for value.
        "chest_movement_rate": rr_i,
    }


def invent(plan: Plan, rng: random.Random, n: int) -> tuple[dict, dict]:
    """One night: the summary entry and the get body that goes with it."""
    start, end = int(plan.into_bed.timestamp()), int(plan.out_of_bed.timestamp())
    minutes = (end - start) // 60
    states = stages(rng, minutes)
    in_bed = [True] * minutes
    for i, (up, back) in enumerate(plan.trips):
        a, b = (int(up.timestamp()) - start) // 60, (int(back.timestamp()) - start) // 60
        for m in range(a, b):
            in_bed[m] = False
        if i in plan.asleep_on_return:
            paint(states, b, b + 6, LIGHT)
        else:
            paint(states, b, b + rng.randint(3, 10), AWAKE)
    if plan.ends_asleep:
        paint(states, minutes - 8, minutes, REM)
    else:
        paint(states, minutes - rng.randint(5, 15), minutes, AWAKE)
    states[0] = AWAKE

    # Runs of one state, cut into short intervals, so neighbours share a state
    # the way they do on a real night.
    intervals: list[dict] = []
    m = 0
    while m < minutes:
        if not in_bed[m]:
            m += 1
            continue
        r = m
        while r < minutes and in_bed[r] and states[r] == states[m]:
            r += 1
        c = m
        while c < r:
            size = min(rng.choice(CHUNK_MINUTES), r - c)
            e = {
                "startdate": start + c * 60,
                "state": states[m],
                "enddate": start + (c + size) * 60,
                "model": MODEL_NAME,
            }
            maps: dict[str, dict[str, int]] = {k: {} for k in METRICS}
            for t in range(e["startdate"], e["enddate"], 60):
                for k, v in vitals(rng, e["state"]).items():
                    maps[k][str(t)] = v
            for k in ("hr", "rr", "snoring", "sdnn_1", "rmssd", "mvt_score", "hrv_quality"):
                e[k] = maps[k]
            e["hash_deviceid"] = DEVICE
            e["model_id"] = MODEL_ID
            e["chest_movement_rate"] = maps["chest_movement_rate"]
            intervals.append(e)
            c += size
        m = r

    ev = events(intervals)
    per = {s: 0 for s in (AWAKE, LIGHT, DEEP, REM)}
    for e in intervals:
        per[e["state"]] += e["enddate"] - e["startdate"]
    tib = sum(per.values())
    tst = per[LIGHT] + per[DEEP] + per[REM]
    asleep_hr = [v for e in intervals if e["state"] in SLEEPING for v in e["hr"].values()]
    rr = [v for e in intervals for v in e["rr"].values()]
    rem_runs = []
    for rem, run in itertools.groupby(intervals, key=lambda e: e["state"] == REM):
        if rem:
            rem_runs.append(sum(e["enddate"] - e["startdate"] for e in run))
    busy = sum(1 for e in intervals for v in e["mvt_score"].values() if v >= 30)

    data = {
        "total_timeinbed": tib,
        "total_sleep_time": tst,
        "lightsleepduration": per[LIGHT],
        "remsleepduration": per[REM],
        "deepsleepduration": per[DEEP],
        "sleep_efficiency": round(tst / tib, 2),
        "sleep_latency": ev["2"][0] - start,
        "wakeup_latency": end - ev["3"][-1],
        "wakeupduration": per[AWAKE],
        "wakeupcount": len(ev["3"]) - 1,
        "waso": sum(a - w for w, a in zip(ev["3"], ev["2"][1:])),
        "nb_rem_episodes": sum(1 for s in rem_runs if s >= 180),
        "out_of_bed_count": len(ev["4"]) - 1,
        "hr_average": half_up(statistics.mean(asleep_hr)),
        "hr_min": min(asleep_hr),
        "hr_max": max(asleep_hr),
        "rr_average": half_up(statistics.mean(rr)),
        "rr_min": min(rr),
        "rr_max": max(rr),
        "breathing_disturbances_intensity": 0,
        "breathing_quality_assessment": 0,
        "snoring": 0,
        "snoringepisodecount": 0,
        "sleep_score": rng.randint(58, 84),
        "night_events": json.dumps({k: deltas(v, start) for k, v in ev.items()},
                                   separators=(",", ":")),
        "apnea_hypopnea_index": 0,
        "mvt_score_avg": rng.randint(3, 5),
        "mvt_active_duration": busy * 60,
    }
    summary = {
        "id": 7_000_000_000 + n * 2_718_281,
        "timezone": "Europe/London",
        "model": MODEL,
        "model_id": MODEL_ID,
        "hash_deviceid": DEVICE,
        "startdate": start,
        "enddate": end,
        "date": plan.out_of_bed.strftime("%Y-%m-%d"),
        "data": data,
        "completed": True,
        # A minute or two after the first time out of bed, and again a day later.
        "created": ev["4"][0] + rng.randint(65, 110),
        "modified": end + rng.randint(9 * 3600, 22 * 3600),
    }
    return summary, {"series": intervals, "model": MODEL}


# --- The rules -----------------------------------------------------------------


def rules(night: dict, body: dict) -> list[str]:
    """Every rule seven real nights obeyed. Returns the ones this night breaks."""
    broken: list[str] = []

    def need(ok: bool, rule: str) -> None:
        if not ok:
            broken.append(rule)

    d = night["data"]
    start, end = night["startdate"], night["enddate"]
    s = sorted(body.get("series") or [], key=lambda e: e["startdate"])
    if not s:
        return ["get came back with no intervals"]

    need(night["model"] == MODEL and body.get("model") == MODEL, "model is 32 on summary and body")
    need(all(e["model"] == MODEL_NAME for e in s), "model is the name on every interval")
    need(night["model_id"] == MODEL_ID and all(e["model_id"] == MODEL_ID for e in s),
         "model_id is 63 everywhere")
    need(all(e["hash_deviceid"] == night["hash_deviceid"] for e in s),
         "the summary's hash on every interval")
    need(isinstance(night["completed"], bool), "completed is a boolean")
    if not isinstance(d.get("night_events"), str):
        # Every rule below about events needs them, so there is no point going on.
        return [*broken, "night_events is JSON inside a string"]

    need(s[0]["startdate"] == start and s[-1]["enddate"] == end,
         "intervals start and end with the summary")
    need(s[0]["state"] == AWAKE, "the first interval is awake")
    need(all(e["startdate"] % 60 == 0 and e["enddate"] % 60 == 0
             and 60 <= e["enddate"] - e["startdate"] <= 600 for e in s),
         "intervals are whole minutes, one to ten of them")
    need(all(b["startdate"] >= a["enddate"] for a, b in zip(s, s[1:])), "no overlaps")
    need(any(a["state"] == b["state"] and a["enddate"] == b["startdate"] for a, b in zip(s, s[1:])),
         "neighbours share a state: an interval is not a stage")

    ev = {k: list(itertools.accumulate(v, initial=start))[1:]
          for k, v in json.loads(d["night_events"]).items()}
    need(set(ev) == {"1", "2", "3", "4"}, "four kinds of event")
    into, asleep, woke, out = ev["1"], ev["2"], ev["3"], ev["4"]
    need(into[0] == start and out[-1] == end, "into bed at startdate, out at enddate")
    need(asleep[0] - start == d["sleep_latency"], "first asleep is sleep_latency")
    need(end - woke[-1] == d["wakeup_latency"], "length less last woke is wakeup_latency")
    need(len(asleep) == len(woke) and all(a < w for a, w in zip(asleep, woke)),
         "asleep and woke alternate")
    need(sum(w - a for a, w in zip(asleep, woke)) == d["total_sleep_time"],
         "asleep spans add up to total_sleep_time")
    need(sum(a - w for w, a in zip(woke, asleep[1:])) == d["waso"], "awake between them is waso")
    need(len(woke) - 1 == d["wakeupcount"], "woke up wakeupcount + 1 times")

    gaps = [(a["enddate"], b["startdate"])
            for a, b in zip(s, s[1:]) if a["enddate"] != b["startdate"]]
    need(gaps == list(zip(out, into[1:])), "every gap is a time out of bed, and only those")
    need(len(gaps) == d["out_of_bed_count"], "out_of_bed_count is the number of gaps")
    need(end - start - sum(b - a for a, b in gaps) == d["total_timeinbed"],
         "total_timeinbed is the length less the gaps")
    need(ev == events(s), "events follow from the intervals")

    per = {st: sum(e["enddate"] - e["startdate"] for e in s if e["state"] == st)
           for st in (AWAKE, LIGHT, DEEP, REM)}
    need((per[AWAKE], per[LIGHT], per[DEEP], per[REM])
         == (d["wakeupduration"], d["lightsleepduration"], d["deepsleepduration"],
             d["remsleepduration"]), "per-state sums match the summary")
    need(per[LIGHT] + per[DEEP] + per[REM] == d["total_sleep_time"],
         "light, deep and REM add up to total_sleep_time")
    need(d["sleep_efficiency"] == round(d["total_sleep_time"] / d["total_timeinbed"], 2),
         "sleep_efficiency is sleep over time in bed")

    minutes = [set(e["hr"]) for e in s]
    need(all(isinstance(e.get(k), dict) and set(e[k]) == m
             for e, m in zip(s, minutes) for k in METRICS),
         "every metric on the same minutes")
    need(all(k.isdigit() and isinstance(v, int)
             for e in s for k2 in METRICS for k, v in (e.get(k2) or {}).items()),
         "timestamps are text, values are whole numbers")
    need(all(set(e["hr"]) == {str(t) for t in range(e["startdate"], e["enddate"], 60)} for e in s),
         "one sample a minute, from startdate up to but not including enddate")
    need(all(e["chest_movement_rate"] == e["rr"] for e in s), "chest_movement_rate is rr")
    need(all(e["rmssd"][k] == 0 for e in s for k, v in e["sdnn_1"].items() if v == 0),
         "where sdnn_1 is 0, rmssd is 0 too")

    asleep_hr = [v for e in s if e["state"] in SLEEPING for v in e["hr"].values()]
    rr = [v for e in s for v in e["rr"].values()]
    need((min(asleep_hr), max(asleep_hr)) == (d["hr_min"], d["hr_max"]),
         "hr_min and hr_max are over sleeping minutes")
    need((min(rr), max(rr)) == (d["rr_min"], d["rr_max"]),
         "rr_min and rr_max are over every minute")
    need(60 <= night["created"] - out[0] <= 120, "created a minute or two after first out of bed")
    return broken


# --- Writing, and checking ------------------------------------------------------


def dump(obj: object) -> bytes:
    """Compact, with slashes escaped, the way Withings sends it."""
    return json.dumps(obj, separators=(",", ":")).replace("/", "\\/").encode()


def write() -> None:
    rng = random.Random(SEED)
    nights = []
    for n, plan in enumerate(PLANS, 1):
        summary, body = invent(plan, rng, n)
        broken = rules(summary, body)
        if broken:
            sys.exit(f"{RED}Refusing to write {summary['date']} ({plan.why}):{RESET} "
                     + "; ".join(broken))
        nights.append((plan, summary, body))

    OUT.mkdir(parents=True, exist_ok=True)
    reply = {"status": 0, "body": {"series": [s for _, s, _ in nights], "more": False, "offset": 0}}
    (OUT / "getsummary.json").write_bytes(dump(reply))
    for plan, summary, body in nights:
        (OUT / f"get-{summary['date']}.json").write_bytes(dump({"status": 0, "body": body}))
        print(f"{GREEN}{summary['date']}{RESET}  {plan.why:<36} "
              f"{len(body['series'])} intervals, every rule holds")
    print(f"\nWritten to {OUT.relative_to(ROOT)}")


def check(folder: Path) -> None:
    """The same rules, over a real capture. Nothing is written."""
    summaries = [n for f in sorted(folder.glob("*getsummary*.json"))
                 for n in json.loads(f.read_bytes())["body"]["series"]]
    bodies = {}
    for f in folder.glob("*get-*.json"):
        found = re.search(r"get-(\d{4}-\d{2}-\d{2})", f.name)
        if found:
            bodies[found.group(1)] = json.loads(f.read_bytes())["body"]
    if not summaries:
        sys.exit(f"No getsummary responses in {folder}")
    failed = 0
    for night in summaries:
        body = bodies.get(night["date"])
        if body is None:
            print(f"{RED}{night['date']}{RESET}  no get response saved for this night")
            failed += 1
            continue
        broken = rules(night, body)
        failed += bool(broken)
        if broken:
            print(f"{RED}{night['date']}{RESET}  breaks: " + "; ".join(broken))
        else:
            print(f"{GREEN}{night['date']}{RESET}  every rule holds")
    if failed:
        sys.exit(f"\n{BOLD}{failed} night(s) broke a rule.{RESET} The fixtures, the parser and "
                 "docs/withings.md are built on these, so find out why before trusting either.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", metavar="FOLDER", help="check a capture folder instead")
    args = parser.parse_args()
    if args.check:
        check(Path(args.check).expanduser())
    else:
        write()


if __name__ == "__main__":
    main()
