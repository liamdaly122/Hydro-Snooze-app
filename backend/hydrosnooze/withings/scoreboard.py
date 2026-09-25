"""The scoreboard: each temperature a part of the night has run at, and what the
mat measured on those nights.

What each night ran comes from night_runs (trials.py). What the mat measured is
read fresh from sleep_nights, because Withings goes on changing a night for most
of the next day. The two meet on the morning.

Deep and REM are scored on deep sleep and REM together, because that is what
Autopilot is told to push for: a colder Deep that buys ten minutes of deep sleep
by costing fifteen of REM is not a win, and scoring each part on its own stage
would call it one. The split is kept beside each setting so the trade can be
seen. Drift is scored on the one thing it exists for:

    Deep    deep sleep and REM together, more is better
    REM     deep sleep and REM together, more is better
    Drift   time to fall asleep, less is better

Wake is not scored. What it is for is how waking up feels, and nothing the mat
measures says that.

**It never says a setting is better on too little.** Deep sleep swings by tens
of minutes from one night to the next for reasons that have nothing to do with
the bed, so two settings are only called apart when the gap between their
averages is more than twice what that swing alone could explain at their number
of nights (a Welch standard error). Until then it says "not sure yet", which for
the first months is the honest answer almost every time.

**It says what went with what, not what caused what.** Nights at one setting can
have been warmer ones in the room, or later ones to bed, so each setting carries
its median room temperature beside its score. The evening suggestions
(suggest.py) are what make the comparison fair, by moving one part a degree at a
time and keeping everything else where it was.

Nights that do not count, per part: a part whose setting was not held for most
of it, or that somebody changed by hand (trials.PartRun.counts), and a night the
mat has no record of or that was under three hours asleep.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..db import Database, StoredNight
from ..trials import NightRun
from .health import _int

#: How far back it looks. Long enough for a slow run of test nights to add up,
#: short enough that one season is not scored against another.
SCORE_DAYS = 120

#: Nights at a setting before its average is compared with anything.
SETTING_NEEDS = 5

#: How many standard errors apart two averages have to be to be called apart.
CLEAR_AT = 2.0

#: Shorter than this is a nap, not a night.
MIN_ASLEEP_S = 3 * 60 * 60


@dataclass(frozen=True)
class _Scored:
    part: str
    label: str
    measure: str
    #: Added together for the score.
    fields: tuple[str, ...]
    more_is_better: bool


#: What Autopilot pushes for: deep sleep and REM, together.
TOGETHER = ("deepsleepduration", "remsleepduration")

PARTS = (
    _Scored("deep", "Deep", "Deep and REM sleep", TOGETHER, True),
    _Scored("rem", "REM", "Deep and REM sleep", TOGETHER, True),
    _Scored("drift", "Drift", "Time to fall asleep", ("sleep_latency",), False),
)


def scoreboard(db: Database, today: date) -> dict[str, Any]:
    first = (today - timedelta(days=SCORE_DAYS)).isoformat()
    runs = db.night_runs(first, today.isoformat())

    # Each recorded night with the mat's side of it, where the mat has one.
    paired: list[tuple[NightRun, StoredNight]] = []
    for run in runs:
        night = db.sleep_night_on(run.wake_on)
        if night is None or (_int(night.data.get("total_sleep_time")) or 0) < MIN_ASLEEP_S:
            continue
        paired.append((run, night))

    return {
        "window_days": SCORE_DAYS,
        "recorded": len(runs),
        "nights": len(paired),
        "tests": sum(1 for run, _ in paired if run.test_part is not None),
        "setting_needs": SETTING_NEEDS,
        "parts": [_part(scored, paired) for scored in PARTS],
    }


def _part(scored: _Scored, paired: list[tuple[NightRun, StoredNight]]) -> dict[str, Any]:
    by_setting: dict[int, list[tuple[NightRun, StoredNight, float | None, int]]] = {}
    for run, night in paired:
        part = run.part(scored.part)
        each = [_int(night.data.get(f)) for f in scored.fields]
        value = None if any(v is None for v in each) else sum(each)
        if part is None or not part.counts or value is None or part.set_c is None:
            continue
        by_setting.setdefault(part.set_c, []).append((run, night, part.bed_c, value))

    settings = [_setting(scored.part, set_c, rows) for set_c, rows in sorted(by_setting.items())]
    out: dict[str, Any] = {
        "part": scored.part,
        "label": scored.label,
        "measure": scored.measure,
        "more_is_better": scored.more_is_better,
        "settings": settings,
        "verdict": "empty" if not settings else "one_setting" if len(settings) == 1 else "not_sure",
        "leader_c": None,
        "runner_c": None,
        "gap_s": None,
        "swing_s": None,
    }

    ready = [s for s in settings if s["nights"] >= SETTING_NEEDS]
    if len(ready) < 2:
        return out

    ranked = sorted(ready, key=lambda s: s["mean_s"], reverse=scored.more_is_better)
    leader, runner = ranked[0], ranked[1]
    gap = abs(leader["mean_s"] - runner["mean_s"])
    se = math.sqrt(leader["sd_s"] ** 2 / leader["nights"] + runner["sd_s"] ** 2 / runner["nights"])
    out.update(
        verdict="clear" if gap > 0 and gap > CLEAR_AT * se else "not_sure",
        leader_c=leader["set_c"],
        runner_c=runner["set_c"],
        gap_s=round(gap),
        swing_s=round(CLEAR_AT * se),
    )
    return out


def _setting(
    part: str, set_c: int, rows: list[tuple[NightRun, StoredNight, float | None, int]]
) -> dict[str, Any]:
    values = [v for *_, v in rows]
    rooms = [run.room_c for run, *_ in rows if run.room_c is not None]
    beds = [bed for _, _, bed, _ in rows if bed is not None]
    low, high = (
        statistics.quantiles(values, n=4, method="inclusive")[0::2]
        if len(values) >= 2
        else (values[0], values[0])
    )
    return {
        "set_c": set_c,
        "nights": len(values),
        # Tests of this part only. A night testing REM ran Deep as usual.
        "tests": sum(1 for run, *_ in rows if run.test_part == part),
        "mean_s": round(statistics.fmean(values)),
        "sd_s": round(statistics.stdev(values)) if len(values) >= 2 else 0,
        "low_s": round(low),
        "high_s": round(high),
        # What else was going on at this setting, so a warm week cannot pass
        # for a good temperature.
        "room_c": round(statistics.median(rooms), 1) if rooms else None,
        "bed_c": round(statistics.fmean(beds), 1) if beds else None,
        # The things a setting must not make worse, whatever it is scored on.
        "awake_s": _median(_int(n.data.get("wakeupduration")) for _, n, *_ in rows),
        "asleep_after_s": _median(_int(n.data.get("sleep_latency")) for _, n, *_ in rows),
        # The two halves of the score, so a trade between them can be seen.
        "deep_s": _median(_int(n.data.get("deepsleepduration")) for _, n, *_ in rows),
        "rem_s": _median(_int(n.data.get("remsleepduration")) for _, n, *_ in rows),
    }


def _median(values) -> int | None:
    held = [v for v in values if v is not None]
    return round(statistics.median(held)) if held else None
