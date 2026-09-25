"""When I actually sleep, set against the parts of the night the bed runs.

Step one of a cleverer Autopilot. The bed runs its night by the clock: Drift from
lights out, then Deep, REM and Wake, each until a time on the wall. The mat says
when deep sleep and REM really happened. This lays the recent nights over the
schedule's clock and says whether the parts sit where the sleep does.

**It suggests and never changes anything.** Taking a suggestion is a tap on the
Autopilot screen, and the same buttons on the Schedule screen move it back.

**Measured from the schedule's lights out, not from falling asleep.** The bed
changes at a time on the clock whatever I am doing, so the question worth asking
is where on that clock my deep sleep falls. A night I went to bed late is a night
the Deep part started before I was asleep, and that belongs in the answer.

Only nights ending on a morning the schedule runs, because the others are a
different routine, and only the last TIMING_NIGHTS of those within TIMING_DAYS,
because a routine moves with the seasons. Nights under MIN_ASLEEP_S are left out:
a nap says nothing about where deep sleep falls in a night.

The night the clocks go back is measured in real minutes from lights out, while
the schedule's parts are wall-clock times, so after 02:00 that one night sits an
hour out. It is one night in thirty and every figure here is a median.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from ..db import Database, StoredNight
from ..models import MIN_STAGE_MINUTES, STAGE_LABEL, Schedule, Stage
from .health import _int, _onset_and_wake, _zone
from .parse import AWAKE, DEEP, LIGHT, REM, SLEEPING, STATE_NAMES

#: How many nights, and how far back to look for them.
TIMING_NIGHTS = 30
TIMING_DAYS = 60

#: Nights before the pattern is drawn, and before a boundary is suggested. Two
#: weeks for a suggestion, because the point is a pattern and three nights of
#: anything is an anecdote.
TIMING_SHOWS = 3
TIMING_NEEDS = 14

#: Shorter than this is a nap, not a night.
MIN_ASLEEP_S = 3 * 60 * 60

#: The pattern is drawn in steps this long.
BIN_MIN = 10

#: "Mostly done": the point by which this share of a night's deep sleep had
#: happened. Not all of it, because a few minutes of deep sleep often turn up
#: late in the night, and a Deep part stretched to cover them would keep the bed
#: cold through the REM it is meant to be warmer for.
DEEP_MOSTLY_DONE = 0.8

#: A boundary is only suggested when the middle half of the nights agree to
#: within this. Wider, and one time would be wrong on too many of them.
STEADY_SPREAD_MIN = 60

#: Boundaries move in the Schedule screen's step, so a suggestion can always be
#: walked back with the same buttons.
STEP_MIN = MIN_STAGE_MINUTES


@dataclass(frozen=True)
class _Night:
    """One night, in minutes from that evening's lights out."""

    asleep_min: int | None
    deep_done_min: int | None
    #: Minutes in each state within each bin, keyed by state.
    bins: dict[int, list[int]]


def timing(db: Database, schedule: Schedule, today: date) -> dict[str, Any]:
    """The recent nights against the schedule as it stands."""
    parts = _parts(schedule)
    night_minutes = schedule.night_minutes
    width = max(1, math.ceil(night_minutes / BIN_MIN))

    nights = [
        _measure(db, n, schedule, width)
        for n in _recent(db, schedule, today)
    ]

    out: dict[str, Any] = {
        "nights": len(nights),
        "shows_at": TIMING_SHOWS,
        "suggests_at": TIMING_NEEDS,
        "lights_out": schedule.bed_time.strftime("%H:%M"),
        "wake": schedule.wake_time.strftime("%H:%M"),
        "night_minutes": night_minutes,
        "bin_min": BIN_MIN,
        "parts": parts,
        "profile": None,
        "boundaries": [],
    }
    if len(nights) < TIMING_SHOWS or not parts:
        return out

    out["profile"] = {
        STATE_NAMES[state]: [
            round(sum(n.bins[state][i] for n in nights) / (len(nights) * BIN_MIN), 3)
            for i in range(width)
        ]
        for state in (DEEP, REM, LIGHT, AWAKE)
    }

    ends = {p["part"]: p["ends_min"] for p in parts}
    measured = {
        Stage.DRIFT: _spread([n.asleep_min for n in nights if n.asleep_min is not None]),
        Stage.DEEP: _spread([n.deep_done_min for n in nights if n.deep_done_min is not None]),
    }
    wanted = {
        stage: _snap(spread["median_min"], ends[stage.value])
        for stage, spread in measured.items()
        if spread is not None
        and len(nights) >= TIMING_NEEDS
        and spread["high_min"] - spread["low_min"] <= STEADY_SPREAD_MIN
    }
    wanted = _fit(wanted, ends)

    for stage in (Stage.DRIFT, Stage.DEEP):
        spread = measured[stage]
        steady = (
            None
            if spread is None
            else spread["high_min"] - spread["low_min"] <= STEADY_SPREAD_MIN
        )
        suggest = wanted.get(stage)
        out["boundaries"].append(
            {
                "part": stage.value,
                "label": STAGE_LABEL[stage],
                "ends_min": ends[stage.value],
                "measured": spread,
                "steady": steady,
                "suggest_min": suggest if suggest != ends[stage.value] else None,
            }
        )
    return out


# --- Which nights ------------------------------------------------------------------


def _recent(db: Database, schedule: Schedule, today: date) -> list[StoredNight]:
    """The nights worth measuring, oldest first, one per morning."""
    days = set(schedule.days_of_week)
    held: dict[str, StoredNight] = {}
    for n in db.sleep_nights(
        (today - timedelta(days=TIMING_DAYS)).isoformat(), today.isoformat()
    ):
        if days and date.fromisoformat(n.wake_on).weekday() not in days:
            continue
        if n.wake_on not in held or _length(n) > _length(held[n.wake_on]):
            held[n.wake_on] = n
    kept = [n for n in held.values() if _asleep_s(db, n) >= MIN_ASLEEP_S]
    return sorted(kept, key=lambda n: n.start_at)[-TIMING_NIGHTS:]


def _length(n: StoredNight) -> int:
    return n.end_at - n.start_at


def _asleep_s(db: Database, n: StoredNight) -> int:
    told = _int(n.data.get("total_sleep_time"))
    if told is not None:
        return told
    return sum(s.end_at - s.start_at for s in db.sleep_stages(n.id) if s.state in SLEEPING)


# --- One night ------------------------------------------------------------------------


def _measure(db: Database, night: StoredNight, schedule: Schedule, width: int) -> _Night:
    tz = _zone(night.timezone)
    lights_out = _lights_out(night, schedule, tz)

    bins = {state: [0] * width for state in (AWAKE, LIGHT, DEEP, REM)}
    deep: list[int] = []
    for m in db.sleep_minutes(night.id):
        offset = (m.at - lights_out) // 60
        if m.state == DEEP:
            deep.append(offset)
        i = offset // BIN_MIN
        if 0 <= offset < schedule.night_minutes and m.state in bins:
            bins[m.state][i] += 1

    onset, _ = _onset_and_wake(night, db.sleep_stages(night.id))
    deep.sort()
    return _Night(
        asleep_min=None if onset is None else round((onset - lights_out) / 60),
        deep_done_min=deep[math.ceil(DEEP_MOSTLY_DONE * len(deep)) - 1] if deep else None,
        bins=bins,
    )


def _lights_out(night: StoredNight, schedule: Schedule, tz: tzinfo | None) -> int:
    """That evening's lights out as the schedule has it, as a unix time.

    Worked backwards from the wake time the way the scheduler does it, so a
    lights out after midnight lands on the right day.
    """
    wake = datetime.combine(date.fromisoformat(night.wake_on), schedule.wake_time)
    naive = wake - timedelta(minutes=schedule.night_minutes)
    return int((naive.replace(tzinfo=tz) if tz else naive).timestamp())


# --- The answer ------------------------------------------------------------------------


def _parts(schedule: Schedule) -> list[dict[str, Any]]:
    out, cursor = [], 0
    for s in schedule.stages:
        out.append(
            {
                "part": s.stage.value,
                "label": STAGE_LABEL[s.stage],
                "starts_min": cursor,
                "ends_min": cursor + s.duration_minutes,
                "temp_c": s.temp_c,
            }
        )
        cursor += s.duration_minutes
    return out


def _spread(values: list[int]) -> dict[str, int] | None:
    """The median, and the middle half of the nights either side of it."""
    if not values:
        return None
    if len(values) < 2:
        return {"median_min": values[0], "low_min": values[0], "high_min": values[0]}
    low, _, high = statistics.quantiles(values, n=4, method="inclusive")
    return {
        "median_min": round(statistics.median(values)),
        "low_min": round(low),
        "high_min": round(high),
    }


def _snap(target: int, current: int) -> int:
    """The nearest time to `target` a whole number of steps from `current`."""
    return current + STEP_MIN * math.floor((target - current) / STEP_MIN + 0.5)


def _fit(wanted: dict[Stage, int], ends: dict[str, int]) -> dict[Stage, int]:
    """Keep every part at least a step long, with REM's end where it is.

    Only Drift's and Deep's ends are ever suggested. REM ends where Wake begins,
    and when to start warming up for the morning is a question about the alarm,
    not about sleep stages.
    """
    drift = wanted.get(Stage.DRIFT, ends[Stage.DRIFT.value])
    deep = wanted.get(Stage.DEEP, ends[Stage.DEEP.value])
    rem_ends = ends[Stage.REM.value]

    deep = min(deep, rem_ends - STEP_MIN)
    drift = max(STEP_MIN, min(drift, deep - STEP_MIN))
    # Squeezed past the point of making sense, say nothing rather than
    # something the Schedule screen would refuse.
    if drift < STEP_MIN or deep - drift < STEP_MIN:
        return {}

    out = {}
    if Stage.DRIFT in wanted or drift != ends[Stage.DRIFT.value]:
        out[Stage.DRIFT] = drift
    if Stage.DEEP in wanted:
        out[Stage.DEEP] = deep
    return out
