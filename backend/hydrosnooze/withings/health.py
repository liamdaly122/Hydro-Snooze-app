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

**And the bed, beside the sleeper**, which is what the whole integration is for.
The probes' temperature for every minute in bed, against what the bed was being
asked for, and what it averaged in each state of sleep. See _bed.

Built on request rather than stored, like the Autopilot report. Everything it
reads was written down when Withings sent it, so a stored copy would know
nothing more and would be one more thing to migrate.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import Database, Sample, StoredNight
from .parse import AWAKE, SLEEPING, STATE_NAMES

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

#: What "usual" means where a night is set against it on the Autopilot screen:
#: the median of up to USUAL_NIGHTS nights before it, once there are USUAL_NEEDS.
USUAL_NIGHTS = 14
USUAL_NEEDS = 3


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
        "bed": _bed(db, night, tz),
    }


# --- The bed, beside the sleeper ----------------------------------------------------


def _bed(db: Database, night: StoredNight, tz: tzinfo | None) -> dict[str, Any]:
    """The bed's temperature for every minute in bed, and in each state of sleep.

    The bed is the return hose: the water that has just been through it. The
    outgoing hose stands in when the return probe is quiet, which is the same
    rule the live readings use (Probes.bed_c).

    A minute grid from getting into bed to getting out for the last time, so it
    lines up with the stages minute for minute. A minute the probes said nothing
    in is None, and the chart leaves a gap there rather than drawing across it.
    Time out of bed is on the grid too: what the bed did while nobody was in it
    is worth seeing.
    """
    start, end = night.start_at, night.end_at
    width = max(0, (end - start) // 60)
    rows = db.samples_as_written(_local(start, tz) - timedelta(minutes=2),
                                 _local(end, tz) + timedelta(minutes=2))

    sums = [0.0] * width
    counts = [0] * width
    target: list[int | None] = [None] * width
    for at, sample in _as_unix(rows, tz, start, end):
        i = int((at - start) // 60)
        if not 0 <= i < width:
            continue
        bed = sample.return_c if sample.return_c is not None else sample.flow_c
        if bed is not None:
            sums[i] += bed
            counts[i] += 1
        if sample.target_c is not None:
            target[i] = sample.target_c
    bed_c = [round(sums[i] / counts[i], 2) if counts[i] else None for i in range(width)]

    # Which state each minute was in, from the mat. A minute with no state is a
    # minute out of bed.
    state: list[int | None] = [None] * width
    for m in db.sleep_minutes(night.id):
        i = (m.at - start) // 60
        if 0 <= i < width:
            state[i] = m.state

    by_stage: dict[str, dict[str, Any]] = {}
    for key, want in [(STATE_NAMES[s], s) for s in (AWAKE, *sorted(SLEEPING))] + [
        ("out_of_bed", None)
    ]:
        picked = [i for i in range(width) if state[i] == want]
        read = [bed_c[i] for i in picked if bed_c[i] is not None]
        if not picked:
            continue
        by_stage[key] = {
            "mean_c": round(sum(read) / len(read), 1) if read else None,
            "minutes": len(read),
            "of": len(picked),
        }

    return {
        "starts_at": _iso(start, tz),
        "step_s": 60,
        "bed_c": bed_c,
        "target_c": target,
        "by_stage": by_stage,
        "measured": any(v is not None for v in bed_c),
    }


def _local(ts: int, tz: tzinfo | None) -> datetime:
    """A unix time as the local time a power sample would have been stamped with."""
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


def _as_unix(
    samples: list[Sample], tz: tzinfo | None, start: int, end: int
) -> list[tuple[float, Sample]]:
    """Each sample's local time as a unix time, the clocks going back included.

    Inside the repeated hour a local time means two moments an hour apart. The
    samples arrive in the order they were taken, so each is given the earlier of
    its two meanings that does not go back before the sample in front of it. The
    first is given whichever meaning falls in the night. Outside that hour there
    is only one meaning and nothing to choose.
    """
    out: list[tuple[float, Sample]] = []
    last: float | None = None
    for sample in samples:
        naive = sample.at.replace(tzinfo=None)
        meanings = sorted(
            {
                (naive.replace(tzinfo=tz, fold=f) if tz else naive.replace(fold=f)).timestamp()
                for f in (0, 1)
            }
        )
        if last is None:
            pick = next((t for t in meanings if start - 3600 <= t <= end + 3600), meanings[0])
        else:
            pick = next((t for t in meanings if t >= last - 1), meanings[-1])
        out.append((pick, sample))
        last = pick
    return out


# --- Against usual, for the Autopilot screen --------------------------------------


@dataclass(frozen=True)
class AgainstUsual:
    """One measurement from the mat, and how it compares with my usual.

    Says what changed and never why. The Autopilot screen it appears on is about
    what the bed did, and a night with more deep sleep after a colder Deep stage
    is two facts side by side, not a result. Enough nights of the join above will
    say whether one follows the other; one night never can.
    """

    key: str
    label: str
    seconds: int
    #: The median of the nights before, or None until there are USUAL_NEEDS.
    usual_seconds: int | None
    nights: int
    change_pct: int | None
    #: More deep and more REM is better; less time to fall asleep is better.
    #: None with nothing to compare against, or no change.
    better: bool | None


#: What the Autopilot screen shows in place of the invented boosts, in its order.
_AGAINST = (
    ("deep", "deepsleepduration", "Deep sleep", True),
    ("rem", "remsleepduration", "REM sleep", True),
    ("asleep", "sleep_latency", "Time to fall asleep", False),
)


def against_usual(db: Database, wake_on: str) -> list[AgainstUsual]:
    """The night ending on `wake_on`, set against the nights before it.

    Empty when the mat has no night for that morning, which is every morning
    before it went in and any morning it missed.
    """
    night = db.sleep_night_on(wake_on)
    if night is None:
        return []
    morning = date.fromisoformat(wake_on)
    before = db.sleep_nights(
        (morning - timedelta(days=60)).isoformat(), (morning - timedelta(days=1)).isoformat()
    )[-USUAL_NIGHTS:]

    out = []
    for key, field, label, more_is_better in _AGAINST:
        seconds = _int(night.data.get(field))
        if seconds is None:
            continue
        earlier = [v for v in (_int(n.data.get(field)) for n in before) if v is not None]
        usual = round(statistics.median(earlier)) if len(earlier) >= USUAL_NEEDS else None
        change = None if not usual else round(100 * (seconds - usual) / usual)
        better = None if usual is None or seconds == usual else (seconds > usual) == more_is_better
        out.append(AgainstUsual(key, label, seconds, usual, len(earlier), change, better))
    return out


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

