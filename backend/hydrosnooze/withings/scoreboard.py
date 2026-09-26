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
    Wake    how waking up felt, from the morning's rating, higher is better

Wake is scored on the one thing the mat cannot measure: how waking up felt,
one to five, answered on the Health Report the morning after (notes.py). A
morning with no answer is a night that does not count for Wake.

**Some nights are left out whole.** A night tagged Alcohol, Ill, or Someone else
in the bed moves deep sleep and REM by more than a degree on the bed does, so it
is left out of every part, and the count of them is said rather than hidden.

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

from collections import Counter

from ..db import Database, StoredNight
from ..notes import NightNote
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
    #: "seconds" for what the mat measures, "rating" for the morning's answer,
    #: one to five. The figures keep their _s names either way; this says what
    #: they are counted in.
    unit: str = "seconds"


#: What Autopilot pushes for: deep sleep and REM, together.
TOGETHER = ("deepsleepduration", "remsleepduration")

PARTS = (
    _Scored("deep", "Deep", "Deep and REM sleep", TOGETHER, True),
    _Scored("rem", "REM", "Deep and REM sleep", TOGETHER, True),
    _Scored("drift", "Drift", "Time to fall asleep", ("sleep_latency",), False),
    _Scored("wake", "Wake", "How waking up felt", (), True, unit="rating"),
)


def scoreboard(db: Database, today: date) -> dict[str, Any]:
    first = (today - timedelta(days=SCORE_DAYS)).isoformat()
    runs = db.night_runs(first, today.isoformat())
    notes = db.night_notes(first, today.isoformat())

    # Each recorded night with the mat's side of it, where the mat has one, and
    # not tagged with something that moves sleep more than the bed does.
    paired: list[tuple[NightRun, StoredNight]] = []
    left_out: Counter[str] = Counter()
    left_nights = 0
    for run in runs:
        night = db.sleep_night_on(run.wake_on)
        if night is None or (_int(night.data.get("total_sleep_time")) or 0) < MIN_ASLEEP_S:
            continue
        note = notes.get(run.wake_on)
        why = note.left_out_by() if note is not None else []
        if why:
            left_nights += 1
            left_out.update(why)
            continue
        paired.append((run, night))

    return {
        "window_days": SCORE_DAYS,
        "recorded": len(runs),
        "nights": len(paired),
        "tests": sum(1 for run, _ in paired if run.test_part is not None),
        "setting_needs": SETTING_NEEDS,
        "left_out": {"nights": left_nights, "by_tag": dict(left_out)},
        "parts": [_part(scored, paired, notes) for scored in PARTS],
    }


def _part(
    scored: _Scored,
    paired: list[tuple[NightRun, StoredNight]],
    notes: dict[str, NightNote],
) -> dict[str, Any]:
    rating = scored.unit == "rating"
    by_setting: dict[int, list[tuple[NightRun, StoredNight, float | None, float]]] = {}
    for run, night in paired:
        part = run.part(scored.part)
        if rating:
            note = notes.get(run.wake_on)
            value = None if note is None or note.rating is None else float(note.rating)
        else:
            each = [_int(night.data.get(f)) for f in scored.fields]
            value = None if any(v is None for v in each) else float(sum(each))
        if part is None or not part.counts or value is None or part.set_c is None:
            continue
        by_setting.setdefault(part.set_c, []).append((run, night, part.bed_c, value))

    settings = [
        _setting(scored, set_c, rows, notes) for set_c, rows in sorted(by_setting.items())
    ]
    out: dict[str, Any] = {
        "part": scored.part,
        "label": scored.label,
        "measure": scored.measure,
        "more_is_better": scored.more_is_better,
        "unit": scored.unit,
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
    shown = _shown(scored)
    out.update(
        verdict="clear" if gap > 0 and gap > CLEAR_AT * se else "not_sure",
        leader_c=leader["set_c"],
        runner_c=runner["set_c"],
        gap_s=shown(gap),
        swing_s=shown(CLEAR_AT * se),
    )
    return out


def _shown(scored: _Scored):
    """Whole seconds for what the mat measures; tenths of a point for a rating,
    where rounding to whole numbers would call 3.6 and 4.4 the same."""
    return (lambda v: round(v, 1)) if scored.unit == "rating" else round


def _setting(
    scored: _Scored,
    set_c: int,
    rows: list[tuple[NightRun, StoredNight, float | None, float]],
    notes: dict[str, NightNote],
) -> dict[str, Any]:
    part = scored.part
    shown = _shown(scored)
    values = [v for *_, v in rows]
    rooms = [run.room_c for run, *_ in rows if run.room_c is not None]
    beds = [bed for _, _, bed, _ in rows if bed is not None]
    low, high = (
        statistics.quantiles(values, n=4, method="inclusive")[0::2]
        if len(values) >= 2
        else (values[0], values[0])
    )
    said = [notes.get(run.wake_on) for run, *_ in rows]
    felt = [n.felt for n in said if n is not None and n.felt is not None]
    return {
        "set_c": set_c,
        "nights": len(values),
        # Tests of this part only. A night testing REM ran Deep as usual.
        "tests": sum(1 for run, *_ in rows if run.test_part == part),
        "mean_s": shown(statistics.fmean(values)),
        # Kept to two places for a rating, whose swing is a point or so and
        # would round to nothing, or to one, and decide the verdict either way.
        "sd_s": (
            (round(statistics.stdev(values), 2) if scored.unit == "rating" else round(statistics.stdev(values)))
            if len(values) >= 2
            else 0
        ),
        "low_s": shown(low),
        "high_s": shown(high),
        # How the bed felt on these nights, when the morning said. The whole
        # night rather than this part, so it goes with the setting rather than
        # being caused by it, like everything else on the scoreboard.
        "felt": {
            "answered": len(felt),
            "too_warm": felt.count("too_warm"),
            "too_cold": felt.count("too_cold"),
        },
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


def test_result(db: Database, wake_on: str, today: date) -> dict[str, Any] | None:
    """The night ending on `wake_on`, if it was a test: what was tried, what the
    mat measured, and where that leaves the scoreboard. None when it was not one.

    From the morning's record once it is written. Before that, from what was
    decided in the evening, because the Autopilot screen is opened over
    breakfast and the record goes down with the morning report twenty minutes
    after the alarm.

    Set beside the usual's average, never judged on its own. One night says
    almost nothing, and the card says so; the verdict is the scoreboard's.
    """
    run = next(iter(db.night_runs(wake_on, wake_on)), None)
    decided = db.decision_for(wake_on)
    if run is not None and run.test_part is not None and run.test_offset_c is not None:
        name, offset = run.test_part, run.test_offset_c
        part = run.part(name)
        set_c = part.set_c if part else None
        counted: bool | None = bool(part and part.counts)
    elif (
        run is None
        and decided is not None
        and decided.decision == "accepted"
        and decided.test_part is not None
        and decided.test_offset_c is not None
    ):
        name, offset = decided.test_part, decided.test_offset_c
        set_c = decided.temps.get(name)
        counted = None
    else:
        return None
    if set_c is None:
        return None

    # Tagged with something that moves sleep more than the bed does, and left
    # out of the scoreboard with every other night like it.
    note = db.night_note(wake_on)
    left_out = note.left_out_by() if note is not None else []
    if left_out:
        counted = False

    night = db.sleep_night_on(wake_on)
    deep = _int(night.data.get("deepsleepduration")) if night else None
    rem = _int(night.data.get("remsleepduration")) if night else None
    board = scoreboard(db, today)
    scored = next(p for p in board["parts"] if p["part"] == name)
    settings = {s["set_c"]: s for s in scored["settings"]}
    usual_c = set_c - offset
    at_usual, at_test = settings.get(usual_c), settings.get(set_c)
    return {
        "part": name,
        "label": scored["label"],
        "set_c": set_c,
        "usual_c": usual_c,
        "offset_c": offset,
        # None until the morning's record is written; False when the part was
        # changed by hand or its setting was not held, so it does not count.
        "counted": counted,
        # Or when the night was tagged with something that leaves it out.
        "left_out": left_out,
        "deep_s": deep,
        "rem_s": rem,
        "together_s": None if deep is None or rem is None else deep + rem,
        "usual_mean_s": at_usual["mean_s"] if at_usual else None,
        "usual_nights": at_usual["nights"] if at_usual else 0,
        "test_nights": at_test["nights"] if at_test else 0,
        "needs": SETTING_NEEDS,
        "verdict": scored["verdict"],
        "leader_c": scored["leader_c"],
    }
