"""The Health Report: one night of sleep, the way the screen draws it.

Modelled on the Eight Sleep report: a week of scores across the top, one big
score, three tiles, a hypnogram, REM and deep against a target, and the vitals
against what is usual for me. Everything on it is measured by the mat or worked
out from what the mat measured. Nothing is invented, and where something cannot
be known yet, it says "learning" and how many more nights it needs, the same way
the Autopilot screen does.

**Three things on it are choices, not measurements, and they all live in the
constants below** so that changing one is one edit:

    where each verdict starts         SCORE_BANDS, PERCENT_BANDS, SLEPT_BANDS_S
    what REM and deep are held to     REM_TARGET_S, DEEP_TARGET_S
    how Routine is worked out         ROUTINE_*

The numbers under the choices are Withings' own: the score is its sleep_score,
Quality is its sleep_efficiency, and the durations are its.

Built on request rather than stored, like the Autopilot report. Everything it
reads was written down when Withings sent it, so a stored copy would know
nothing more and would be one more thing to migrate.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import Database, StoredNight
from .parse import SLEEPING, STATE_NAMES

# --- The choices ---------------------------------------------------------------

#: Where each verdict starts, highest first. Picked to agree with the screenshots
#: the screen is modelled on: a score of 97 reads Good and 78 reads Fair; tiles of
#: 100% read Excellent, 87% and 89% Good, and 51% Low.
SCORE_BANDS = ((80, "good"), (60, "fair"), (0, "low"))
PERCENT_BANDS = ((95, "excellent"), (75, "good"), (60, "fair"), (0, "low"))
SLEPT_BANDS_S = ((7 * 3600, "good"), (6 * 3600, "fair"), (0, "low"))
WORDS = {"excellent": "Excellent", "good": "Good", "fair": "Fair", "low": "Low"}

#: What REM and deep are held to. The screenshots' own targets.
REM_TARGET_S = 90 * 60
DEEP_TARGET_S = 70 * 60

#: Routine: how close last night's falling asleep and waking up came to usual.
#: Usual is the median of up to ROUTINE_NIGHTS nights before it. Within
#: ROUTINE_FREE_MIN of usual costs nothing, and the score falls in a straight
#: line to nothing at ROUTINE_ZERO_MIN off. Falling asleep and waking count half
#: each.
ROUTINE_NIGHTS = 14
ROUTINE_NEEDS = 3
ROUTINE_FREE_MIN = 15
ROUTINE_ZERO_MIN = 120

#: A vital is "in range" inside the middle 80% of my own last RANGE_NIGHTS nights.
#: Mine rather than a population's, because the only question this can answer
#: honestly is whether last night was usual for me.
RANGE_NIGHTS = 30
RANGE_NEEDS = 7

#: The week along the top starts on Sunday, as the screenshots have it.
WEEK_STARTS_ON = 6  # Monday is 0


# --- Building it -----------------------------------------------------------------


def report(db: Database, wake_on: str | None = None) -> dict[str, Any] | None:
    """The report for the night ending on `wake_on`, or the latest night.

    None when there are no nights at all. A date with no night still gets its
    week, with `night` None, so the week along the top can be stepped through
    past a night the mat missed.
    """
    latest = db.latest_sleep_night()
    if latest is None:
        return None
    night = db.sleep_night_on(wake_on) if wake_on else latest
    anchor = date.fromisoformat(wake_on) if wake_on else date.fromisoformat(latest.wake_on)
    out: dict[str, Any] = {
        "week": _week(db, anchor),
        "earliest": db.earliest_sleep_wake_on(),
        "latest": latest.wake_on,
        "night": None,
    }
    if night is not None:
        out["night"] = _night(db, night)
    return out


def _week(db: Database, anchor: date) -> list[dict[str, Any]]:
    first = anchor - timedelta(days=(anchor.weekday() - WEEK_STARTS_ON) % 7)
    days = [first + timedelta(days=i) for i in range(7)]
    held: dict[str, StoredNight] = {}
    for n in db.sleep_nights(days[0].isoformat(), days[-1].isoformat()):
        # The longest, if a morning has more than one. See Database.sleep_night_on.
        if n.wake_on not in held or _length(n) > _length(held[n.wake_on]):
            held[n.wake_on] = n
    out = []
    for d in days:
        n = held.get(d.isoformat())
        score = _int(n.data.get("sleep_score")) if n else None
        out.append({"date": d.isoformat(), "score": score, "has_night": n is not None})
    return out


def _night(db: Database, night: StoredNight) -> dict[str, Any]:
    tz = _zone(night.timezone)
    d = night.data
    stages = db.sleep_stages(night.id)

    # The nights before this one, for what "usual" means.
    morning = date.fromisoformat(night.wake_on)
    before = db.sleep_nights(
        (morning - timedelta(days=90)).isoformat(), (morning - timedelta(days=1)).isoformat()
    )
    vitals = db.sleep_vitals([night.id] + [n.id for n in before[-RANGE_NIGHTS:]])

    asleep_s = _int(d.get("total_sleep_time")) or sum(
        s.end_at - s.start_at for s in stages if s.state in SLEEPING
    )
    totals = {
        "awake": _int(d.get("wakeupduration")),
        "light": _int(d.get("lightsleepduration")),
        "deep": _int(d.get("deepsleepduration")),
        "rem": _int(d.get("remsleepduration")),
    }
    for state, name in STATE_NAMES.items():
        if totals[name] is None:
            totals[name] = sum(s.end_at - s.start_at for s in stages if s.state == state)

    fell_asleep, woke_up = _onset_and_wake(night, stages)
    score = _int(d.get("sleep_score"))
    efficiency = d.get("sleep_efficiency")
    quality = None if not isinstance(efficiency, (int, float)) else round(efficiency * 100)
    routine, routine_nights = _routine(night, before[-ROUTINE_NIGHTS:], tz)

    hrv_now, rr_now = vitals.get(night.id, (None, None))
    hr_before = [_int(n.data.get("hr_average")) for n in before[-RANGE_NIGHTS:]]
    hrv_before = [vitals.get(n.id, (None, None))[0] for n in before[-RANGE_NIGHTS:]]
    rr_before = [vitals.get(n.id, (None, None))[1] for n in before[-RANGE_NIGHTS:]]

    return {
        "wake_on": night.wake_on,
        "timezone": night.timezone,
        "completed": night.completed,
        "updated_at": _iso(night.modified, tz),
        "in_bed": {"starts_at": _iso(night.start_at, tz), "ends_at": _iso(night.end_at, tz)},
        "fell_asleep_at": _iso(fell_asleep, tz),
        "woke_up_at": _iso(woke_up, tz),
        "score": {"value": score, **_verdict(score, SCORE_BANDS)},
        "tiles": {
            "quality": {
                "percent": quality,
                "means": "Time asleep out of time in bed",
                **_verdict(quality, PERCENT_BANDS),
            },
            "routine": {
                "percent": routine,
                "means": "How close falling asleep and waking came to your usual times",
                **(
                    _verdict(routine, PERCENT_BANDS)
                    if routine is not None
                    else _learning(ROUTINE_NEEDS - routine_nights)
                ),
            },
            "time_slept": {"seconds": asleep_s, **_verdict(asleep_s, SLEPT_BANDS_S)},
        },
        "stages": [
            {
                "stage": STATE_NAMES.get(s.state, "awake"),
                "starts_at": _iso(s.start_at, tz),
                "ends_at": _iso(s.end_at, tz),
            }
            for s in stages
        ],
        "out_of_bed": [
            {"starts_at": _iso(a, tz), "ends_at": _iso(b, tz)}
            for a, b in _out_of_bed(night, stages)
        ],
        "totals": totals,
        "rem": _against(totals["rem"], asleep_s, REM_TARGET_S),
        "deep": _against(totals["deep"], asleep_s, DEEP_TARGET_S),
        "vitals": {
            "heart_rate": _vital(_int(d.get("hr_average")), hr_before, "bpm", 0),
            "hrv": _vital(hrv_now, hrv_before, "ms", 0),
            "breath_rate": _vital(rr_now, rr_before, "/min", 1),
        },
        "breathing": {
            "disturbances": d.get("breathing_disturbances_intensity"),
            "apnea_hypopnea_index": d.get("apnea_hypopnea_index"),
        },
    }


# --- The pieces --------------------------------------------------------------------


def _onset_and_wake(night: StoredNight, stages: list) -> tuple[int | None, int | None]:
    """When I fell asleep and when I finally woke.

    From night_events when Withings sent them, which on every real night agreed
    with sleep_latency and wakeup_latency to the second. From the stages
    otherwise.
    """
    events = night.events or {}
    asleep, woke = events.get("asleep") or [], events.get("woke") or []
    if asleep and woke:
        return asleep[0], woke[-1]
    sleeping = [s for s in stages if s.state in SLEEPING]
    if not sleeping:
        return None, None
    return sleeping[0].start_at, sleeping[-1].end_at


def _out_of_bed(night: StoredNight, stages: list) -> list[tuple[int, int]]:
    """Each time out of bed, as (got up, got back in)."""
    events = night.events or {}
    into, out = events.get("into_bed") or [], events.get("out_of_bed") or []
    if into and out:
        return list(zip(out, into[1:]))
    return [(a.end_at, b.start_at) for a, b in zip(stages, stages[1:]) if a.end_at != b.start_at]


def _routine(
    night: StoredNight, before: list[StoredNight], tz: tzinfo | None
) -> tuple[int | None, int]:
    """Routine as a percentage, and how many earlier nights it was measured against."""
    onset, wake = _onset_and_wake(night, [])
    earlier = [_onset_and_wake(n, []) for n in before]
    earlier = [(a, b) for a, b in earlier if a is not None and b is not None]
    if onset is None or wake is None or len(earlier) < ROUTINE_NEEDS:
        return None, len(earlier)

    def minutes(ts: int) -> int:
        # From noon rather than midnight, so 23:50 and 00:10 are twenty minutes
        # apart rather than twenty-three hours and forty. Local time, because a
        # routine is kept by the clock on the wall, clocks going back included.
        t = datetime.fromtimestamp(ts, tz)
        return (t.hour * 60 + t.minute - 12 * 60) % (24 * 60)

    def kept(now: int, usual: list[int]) -> float:
        off = abs(minutes(now) - statistics.median(usual))
        spent = (off - ROUTINE_FREE_MIN) / (ROUTINE_ZERO_MIN - ROUTINE_FREE_MIN)
        return max(0.0, min(1.0, 1.0 - spent))

    fell = kept(onset, [minutes(a) for a, _ in earlier])
    rose = kept(wake, [minutes(b) for _, b in earlier])
    return round(100 * (fell + rose) / 2), len(earlier)


def _against(seconds: int | None, asleep_s: int, target_s: int) -> dict[str, Any]:
    return {
        "seconds": seconds,
        "percent": None if not seconds or not asleep_s else round(100 * seconds / asleep_s),
        "target_seconds": target_s,
        "met": seconds is not None and seconds >= target_s,
    }


def _vital(now: float | None, before: list, unit: str, places: int) -> dict[str, Any]:
    """A reading, and whether it is inside the middle 80% of my own usual."""

    def shown(v: float) -> float:
        return round(v, places) if places else round(v)

    value = None if now is None else shown(now)
    usual = [v for v in before if v is not None]
    out: dict[str, Any] = {"value": value, "unit": unit, "range": None}
    if len(usual) < RANGE_NEEDS or value is None:
        return {**out, **_learning(RANGE_NEEDS - len(usual))}
    deciles = statistics.quantiles(usual, n=10)
    # Judged as shown. Comparing the rounded reading with the unrounded edges
    # put a night identical to every other night above its own usual: 46 against
    # a range of 45.67 to 45.67, which the screen would print as 46 to 46.
    low, high = shown(deciles[0]), shown(deciles[-1])
    out["range"] = [low, high]
    if value < low:
        return {**out, "verdict": "below", "label": "Below usual"}
    if value > high:
        return {**out, "verdict": "above", "label": "Above usual"}
    return {**out, "verdict": "in_range", "label": "In range"}


def _verdict(value: float | None, bands: tuple[tuple[float, str], ...]) -> dict[str, Any]:
    if value is None:
        return {"verdict": None, "label": None}
    for floor, key in bands:
        if value >= floor:
            return {"verdict": key, "label": WORDS[key]}
    return {"verdict": bands[-1][1], "label": WORDS[bands[-1][1]]}


def _learning(needed: int) -> dict[str, Any]:
    return {"verdict": "learning", "label": "Learning", "nights_needed": max(needed, 1)}


def _int(v: object) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _length(n: StoredNight) -> int:
    return n.end_at - n.start_at


def _zone(name: str | None) -> tzinfo | None:
    try:
        return ZoneInfo(name) if name else None
    except ZoneInfoNotFoundError:
        return None


def _iso(ts: int | None, tz: tzinfo | None) -> str | None:
    """With its offset, so the phone draws it at the time it happened. The night
    the clocks go back has two 01:30s, and only the offset tells them apart."""
    if ts is None:
        return None
    t = datetime.fromtimestamp(ts, tz)
    return (t if tz is not None else t.astimezone()).isoformat()

