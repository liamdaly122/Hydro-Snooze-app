"""What last night actually did, in the few lines worth reading over breakfast.

Every night this system produces a few thousand measurements and, until now,
nobody read any of them. The event log holds everything and is far too long. The
device bar holds this moment and forgets the last eight hours. So the honest
answer to "did that go well?" was to open the app and scroll, which nobody does
on the fourth morning.

This is the other way round. One message, sent once, when the night has finished,
saying whether it worked and what the bed did. If the answer is "yes, fine", that
takes four lines and is worth having anyway, because a run of quiet mornings is
what earns the trust to stop checking.

Nothing here decides anything or touches the unit. It reads what was already
written down and turns it into sentences.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from .db import Sample
from .events import Event, Level
from .hold import TRIM_KIND
from .models import QUIET_KIND, NightPlan

#: Sampling interval, for turning watts into energy. Not read from settings on
#: purpose: this works off what was recorded, and the gap between two rows is the
#: only thing that says how long a reading stood for.
FALLBACK_SAMPLE_S = 30.0

#: A bed this far from its setpoint for a stretch of the night is worth saying
#: out loud rather than leaving in a range nobody parses.
DRIFTED_C = 2.5


@dataclass(frozen=True)
class Report:
    title: str
    body: str
    level: Level

    @property
    def message(self) -> str:
        return self.body


def _times(n: int) -> str:
    """Counting the way a person would. "Twice", not "2 times"."""
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _things(n: int) -> str:
    return "One thing" if n == 1 else f"{n} things"


def kwh(samples: list[Sample]) -> float:
    """Energy over the whole run, from the gaps between the readings themselves.

    Multiplying every sample by a fixed thirty seconds would quietly invent power
    across a gap where the plug was unreachable, and those gaps are exactly the
    nights worth being careful about. So each reading only counts for as long as
    it actually stood, and a gap longer than a few beats counts for one beat.

    Autopilot reads this too. It had its own copy, line for line, which is one
    edit away from the screen and the morning message giving two numbers.
    """
    if len(samples) < 2:
        return 0.0
    total = 0.0
    for now, nxt in pairwise(samples):
        held = (nxt.at - now.at).total_seconds()
        total += now.watts * min(held, FALLBACK_SAMPLE_S * 3)
    return round(total / 3_600_000, 2)


def _bed(samples: list[Sample]) -> list[float]:
    return [s.return_c for s in samples if s.return_c is not None]


def _stages_landed(
    plan: NightPlan, fired: set[str], cancelled: set[str] = frozenset()
) -> tuple[int, list[str], list[str]]:
    """How many stages ran, which failed, and which were called off.

    Three, not two. A stage cancelled by switching automation off is not a
    failure and was counted as one: the night it was asked for read as a night
    that went wrong.
    """
    ran, missed, off = 0, [], []
    for step in plan.steps:
        key = f"stage:{step.stage.value}"
        if key in fired:
            ran += 1
        elif key in cancelled:
            off.append(step.label)
        else:
            missed.append(step.label)
    return ran, missed, off


def stages_line(
    plan: NightPlan, fired: set[str], cancelled: set[str] = frozenset()
) -> tuple[str, Level]:
    """The first line of the report, and whether it is worth a warning."""
    landed, missed, off = _stages_landed(plan, fired, cancelled)
    total = len(plan.steps)
    if not missed and not off:
        return f"All {total} stages landed.", "info"
    parts = [f"{landed} of {total} stages landed."]
    if missed:
        parts.append(f"Missed: {', '.join(missed)}.")
    if off:
        parts.append(f"Cancelled, as asked: {', '.join(off)}.")
    return " ".join(parts), ("warning" if missed else "info")


def _how_the_bed_did(plan: NightPlan, samples: list[Sample]) -> str | None:
    """One sentence about the bed, or None if nothing measured it.

    Deliberately compared against the stage that was running at the time rather
    than against a single number. A night that steps from 24C to 30C has no one
    setpoint, and reporting the spread without saying what it was aiming at makes
    a working night look like a wandering one.
    """
    bed = _bed(samples)
    if not bed:
        return None

    wanted: list[tuple[float, int]] = []
    for sample in samples:
        if sample.return_c is None:
            continue
        step = next(
            (s for s in plan.steps if s.starts_at <= sample.at < s.ends_at), None
        )
        if step is None:
            continue
        # What was being asked for at the time, when it was written down, which
        # is how Autopilot scores the same night. The plan does not know about a
        # stage changed at the bedside or a nudge, so rebuilding from it called
        # a bed that did exactly as asked several degrees off, in the message,
        # while the screen a tap away said it was on target.
        target = sample.target_c if sample.target_c is not None else step.temp_c
        wanted.append((sample.return_c, target))

    low, high = min(bed), max(bed)
    said = f"Bed ran {low:.1f} to {high:.1f}C"
    if not wanted:
        return said + "."

    off = [abs(was - target) for was, target in wanted]
    worst = max(off)
    typical = sum(off) / len(off)
    if worst <= DRIFTED_C:
        return f"{said}, never more than {worst:.1f}C off the stage it was in."
    share = sum(1 for o in off if o > DRIFTED_C) / len(off)
    return (
        f"{said}, and spent {share:.0%} of the night more than {DRIFTED_C}C off "
        f"its stage, {typical:.1f}C off on average."
    )


def _how_it_got_ready(pre) -> str | None:
    if pre is None:
        return None
    minutes = pre.seconds // 60
    how = {"probes": "measured on the hoses", "plug": "measured off the plug"}.get(
        pre.decided_by or "", "measured"
    )
    if not pre.reached:
        return f"Getting ready ran {minutes}m without settling at {pre.target_c}C."
    where = ""
    if pre.start_c is not None and pre.end_c is not None:
        where = f", {pre.start_c:.1f} to {pre.end_c:.1f}C"
    return f"Ready in {minutes}m{where}, {how}."


def build(
    plan: NightPlan,
    samples: list[Sample],
    events: list[Event],
    fired: set[str],
    pre=None,
    *,
    cancelled: set[str] = frozenset(),
) -> Report:
    """The whole night in four or five lines.

    `fired` is the set of job keys that ran, `pre` the pre-conditioning row for
    this night or None. Everything else is read out of what was recorded while it
    happened.
    """
    first, stages_level = stages_line(plan, fired, cancelled)
    bad = [e for e in events if e.level in ("warning", "error")]
    # Deliberately not "mode". That kind covers every set_mode there is: stage
    # boundaries, getting the bed ready, anything pressed by hand. Only the
    # corrections belong in this count.
    swaps = len([e for e in events if e.kind == QUIET_KIND])

    lines: list[str] = []

    lines.append(first)

    for maybe in (_how_it_got_ready(pre), _how_the_bed_did(plan, samples)):
        if maybe:
            lines.append(maybe)

    if swaps:
        lines.append(f"Swapped mode {_times(swaps)} to keep it quiet.")

    trims = len([e for e in events if e.kind == TRIM_KIND])
    if trims:
        lines.append(f"Trimmed the setting {_times(trims)} to hold the number.")

    energy = kwh(samples)
    if energy:
        lines.append(f"{energy} kWh.")

    if bad:
        lines.append(f"{_things(len(bad))} worth a look. First: {bad[0].message}")

    level: Level = "warning" if (stages_level == "warning" or bad) else "info"
    # Named for the screen it belongs to. The push is the trailer and Autopilot
    # is the film: four lines on a lock screen, and the whole night a tap away.
    title = "Autopilot: last night" if level == "info" else "Autopilot: last night, with notes"
    return Report(title=title, body="\n".join(lines), level=level)


def window(plan: NightPlan) -> tuple[datetime, datetime]:
    """The stretch a report covers: from whenever the bed started getting ready.

    Pre-conditioning is part of the night even though it happens before bedtime,
    and an hour of margin on the front catches a lead time longer than the one
    that was planned.
    """
    start = plan.precool_at or plan.bedtime_at
    return start - timedelta(hours=1), plan.wake_at + timedelta(minutes=30)
