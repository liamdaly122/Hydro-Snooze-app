"""What Withings sends, turned into nights, stages and minutes.

Pure functions, no I/O, so every trap in docs/withings.md can be tested against
the invented fixtures without a network or a database. The traps, and where each
is dealt with:

    timestamps arrive as text                    _value, minutes
    the device hash is on every interval         dropped: nothing here keeps it
    absent and null both mean "not available"    _value, and never a zero
    night_events is JSON inside a string         events
    `model` is a number in one place, a name     only model_id is read
      in another
    an interval is not a stage                   stages
    0 heart-rate variability means no reading    minutes

Every time stays a unix timestamp. The power samples are stored in local time
with no zone, and a night that runs through the clocks going back has an hour in
it that happens twice. These do not have that problem, and nothing here gives
it to them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

AWAKE, LIGHT, DEEP, REM = 0, 1, 2, 3
SLEEPING = frozenset((LIGHT, DEEP, REM))

STATE_NAMES = {AWAKE: "awake", LIGHT: "light", DEEP: "deep", REM: "rem"}

#: The four kinds of event in night_events. Withings does not name them; these
#: are what seven real nights showed they mean, checked against six other
#: fields. See docs/withings.md.
EVENT_NAMES = {"1": "into_bed", "2": "asleep", "3": "woke", "4": "out_of_bed"}

#: The per-minute fields kept. chest_movement_rate is not one of them: on every
#: real minute it was rr again, value for value.
MINUTE_FIELDS = ("hr", "rr", "sdnn_1", "rmssd", "hrv_quality", "mvt_score", "snoring")

#: Heart-rate variability of zero is no reading, not a measurement. Nobody has
#: 0 ms of it; moving about is what stops it being measured.
ZERO_MEANS_NONE = frozenset(("sdnn_1", "rmssd"))


@dataclass(frozen=True)
class Stage:
    """One stretch of a single state, however many intervals it came as."""

    start_at: int
    end_at: int
    state: int


@dataclass(frozen=True)
class Minute:
    at: int
    state: int
    hr: int | None = None
    rr: int | None = None
    sdnn_1: int | None = None
    rmssd: int | None = None
    hrv_quality: int | None = None
    mvt_score: int | None = None
    snoring: int | None = None


@dataclass(frozen=True)
class Night:
    id: int
    #: Withings' `date`: the morning the night ended, in its own timezone.
    wake_on: str
    start_at: int
    end_at: int
    timezone: str | None
    modified: int
    completed: bool | None
    #: The summary fields as sent, less night_events, which is below decoded.
    data: dict[str, Any]
    #: into_bed, asleep, woke and out_of_bed, each a list of unix times. None
    #: when Withings sent none, which is what the demo account did.
    events: dict[str, list[int]] | None
    stages: tuple[Stage, ...] = field(default_factory=tuple)
    minutes: tuple[Minute, ...] = field(default_factory=tuple)


def events(raw: object, start: int) -> dict[str, list[int]] | None:
    """night_events, as absolute times.

    It arrives as a string holding JSON. Inside, each kind of event has a list
    of gaps rather than times: the first is seconds after `start`, and each one
    after it is seconds after the previous event of the same kind.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    out: dict[str, list[int]] = {}
    for key, gaps in raw.items():
        name = EVENT_NAMES.get(str(key))
        if name is None or not isinstance(gaps, list):
            # A kind nobody has seen yet. Not a reason to lose the others.
            continue
        at, times = start, []
        for gap in gaps:
            at += int(gap)
            times.append(at)
        out[name] = times
    return out


def stages(intervals: list[dict[str, Any]]) -> tuple[Stage, ...]:
    """Runs of one state, merged.

    Withings cuts the time in bed into pieces of one to ten minutes whatever the
    sleep is doing, and about four in five neighbours share a state. Counting
    those as stages would call a night with twenty changes one with a hundred.

    A run never merges across a gap. A gap is time out of bed, and a stage that
    swallowed it would put me asleep in a bed I was not in.
    """
    out: list[Stage] = []
    for e in sorted(intervals, key=lambda e: e["startdate"]):
        start, end, state = int(e["startdate"]), int(e["enddate"]), int(e["state"])
        if out and out[-1].state == state and out[-1].end_at == start:
            out[-1] = Stage(out[-1].start_at, end, state)
        else:
            out.append(Stage(start, end, state))
    return tuple(out)


def _value(e: dict[str, Any], name: str, key: str) -> int | None:
    """One reading, or None. Absent, null and not a number all come out the same,
    and none of them ever comes out as a zero."""
    series = e.get(name)
    if not isinstance(series, dict):
        return None
    v = series.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v == 0 and name in ZERO_MEANS_NONE:
        return None
    return int(v)


def minutes(intervals: list[dict[str, Any]]) -> tuple[Minute, ...]:
    """One row a minute in bed, carrying the state of the interval it fell in.

    The timestamps are keys, and JSON keys are always text. They are made into
    numbers here, once, because a join that compared "1790000000" with 1790000000
    would match nothing and look exactly like a quiet night.
    """
    out: list[Minute] = []
    for e in sorted(intervals, key=lambda e: e["startdate"]):
        keys: set[str] = set()
        for name in MINUTE_FIELDS:
            series = e.get(name)
            if isinstance(series, dict):
                keys.update(str(k) for k in series)
        for key in sorted(keys, key=int):
            out.append(
                Minute(
                    int(key),
                    int(e["state"]),
                    **{name: _value(e, name, key) for name in MINUTE_FIELDS},
                )
            )
    return tuple(out)


def night(summary: dict[str, Any], intervals: list[dict[str, Any]]) -> Night:
    """One summary from getsummary and the intervals get gave for it."""
    start = int(summary["startdate"])
    data = dict(summary.get("data") or {})
    raw_events = data.pop("night_events", None)
    completed = summary.get("completed")
    return Night(
        id=int(summary["id"]),
        wake_on=str(summary["date"]),
        start_at=start,
        end_at=int(summary["enddate"]),
        timezone=summary.get("timezone"),
        modified=int(summary["modified"]),
        completed=None if completed is None else bool(completed),
        data=data,
        events=events(raw_events, start),
        stages=stages(intervals),
        minutes=minutes(intervals),
    )
