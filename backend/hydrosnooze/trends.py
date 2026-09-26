"""Trends: how the nights are going over weeks and months, not one at a time.

The Health Report is one night and a week strip, History is the last day, and
the scoreboard compares settings rather than weeks. None of them answers the
question under all of it: is sleep getting better, and what is it costing? This
does, with a row per night and the same stretch set against the one before it.

Built on request from what is already stored, like the Health Report and the
Autopilot screen: the mat's nights, what each night ran (night_runs), and how
it felt (night_notes). A night the mat missed is a gap in the sleep figures,
and a night the plug missed is a gap in the energy, never a nought.

Tagged nights stay in. The scoreboard leaves some out because it compares
settings a degree apart; here the question is how the nights are actually going,
and a night after a few drinks is one of them. Each row says what it was tagged,
so the app can mark it.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from .db import Database, StoredNight
from .notes import BY_KEY
from .withings.health import _int

#: The stretches the app offers. Anything else is refused, so a phone cannot ask
#: the Pi to read five years in one go.
RANGES = (30, 90, 365)


def trends(db: Database, today: date, days: int, tariff_p: float | None) -> dict[str, Any]:
    """The last `days` mornings to today, a row for each that has anything, and a
    summary of them against the same number of mornings before."""
    first = today - timedelta(days=days - 1)
    before = first - timedelta(days=days)
    rows = _rows(db, before, today, tariff_p)
    now = [r for r in rows if r["wake_on"] >= first.isoformat()]
    then = [r for r in rows if r["wake_on"] < first.isoformat()]
    return {
        "days": days,
        "first": first.isoformat(),
        "last": today.isoformat(),
        "tariff_p": tariff_p,
        "nights": now,
        "summary": {"now": _summary(now), "before": _summary(then)},
    }


def _rows(db: Database, first: date, last: date, tariff_p: float | None) -> list[dict[str, Any]]:
    a, b = first.isoformat(), last.isoformat()
    runs = {r.wake_on: r for r in db.night_runs(a, b)}
    notes = db.night_notes(a, b)
    # The longest, if a morning has more than one, as the Health Report does.
    mat: dict[str, StoredNight] = {}
    for n in db.sleep_nights(a, b):
        if n.wake_on not in mat or (n.end_at - n.start_at) > (
            mat[n.wake_on].end_at - mat[n.wake_on].start_at
        ):
            mat[n.wake_on] = n

    out = []
    for wake_on in sorted(set(runs) | set(mat)):
        run, night, note = runs.get(wake_on), mat.get(wake_on), notes.get(wake_on)
        data = night.data if night else {}
        deep, rem = _int(data.get("deepsleepduration")), _int(data.get("remsleepduration"))
        kwh = run.kwh if run else None
        out.append(
            {
                "wake_on": wake_on,
                "score": _int(data.get("sleep_score")),
                "asleep_s": _int(data.get("total_sleep_time")),
                "deep_s": deep,
                "rem_s": rem,
                "deep_rem_s": None if deep is None or rem is None else deep + rem,
                "latency_s": _int(data.get("sleep_latency")),
                "bed_c": run.bed_c if run else None,
                "room_c": run.room_c if run else None,
                "kwh": kwh,
                "cost_p": None if kwh is None or tariff_p is None else round(kwh * tariff_p, 1),
                "rating": note.rating if note else None,
                "tags": [BY_KEY[t].label for t in note.tags if t in BY_KEY] if note else [],
                "left_out": bool(note and note.left_out_by()),
                "test": run.test_part if run else None,
            }
        )
    return out


def _mean(rows: list[dict[str, Any]], key: str, places: int = 0) -> float | None:
    values = [r[key] for r in rows if r[key] is not None]
    if not values:
        return None
    mean = statistics.fmean(values)
    return round(mean, places) if places else round(mean)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Averages over the nights that measured each thing, and the totals."""
    kwh = [r["kwh"] for r in rows if r["kwh"] is not None]
    cost = [r["cost_p"] for r in rows if r["cost_p"] is not None]
    return {
        "nights": len(rows),
        "score": _mean(rows, "score"),
        "deep_rem_s": _mean(rows, "deep_rem_s"),
        "latency_s": _mean(rows, "latency_s"),
        "asleep_s": _mean(rows, "asleep_s"),
        "bed_c": _mean(rows, "bed_c", 1),
        "room_c": _mean(rows, "room_c", 1),
        "kwh_per_night": _mean(rows, "kwh", 2),
        "kwh_total": round(sum(kwh), 1) if kwh else None,
        "cost_p_total": round(sum(cost)) if cost else None,
    }
