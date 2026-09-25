"""What each night ran, written down the morning after, for the scoreboard.

Step two of a cleverer Autopilot is learning which temperature in each part of
the night goes with more deep sleep and more REM. That needs two things side by
side for every night: what the bed was set to in each part, and what the mat
measured. The mat's half is already stored (sleep_nights). This is the other
half.

**The setting is read from the record, not the schedule.** Each power sample
carries what the bed was being asked for at that moment (`target_c`: the number
set, a hand nudge included, before the service's own correction). So a part's
setting is whatever was asked for through most of it, which is what actually
happened rather than what the schedule meant to happen. A night with a tonight-
only change, a saved night loaded from Profiles, or a part that never landed all
come out as they really were.

**The mat's numbers are not copied in.** Withings goes on changing a night for
most of the following day, so the scoreboard reads them fresh from
sleep_nights every time it is built. This row holds only what the bed did.

Three reasons a part does not count towards the scoreboard, each worked out
here and kept on the row so the scoreboard never has to guess:

    set_c None     nothing was asked for in it: skipped, away, unit off
    held < HELD    the setting was in force for less than half of it
    by_hand        somebody changed something during it

`rebuilt` marks a night written afterwards from today's schedule times, for
nights from before this existed. The settings are still the recorded ones; only
where each part starts and ends is today's.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Sample
    from .models import NightPlan

#: A part counts only if its setting was in force for at least this share of it.
#: Half, because a part that spent most of its time on something else was a
#: test of something else.
HELD = 0.5


@dataclass(frozen=True)
class PartRun:
    """One part of one night, as it actually ran."""

    part: str
    starts_at: datetime
    ends_at: datetime
    #: What was asked for through most of the part, or None if nothing was.
    set_c: int | None
    #: The share of the part's readings asking for set_c, 0 to 1.
    held: float
    #: What the bed averaged over the part, off the return hose. None when the
    #: probes said nothing.
    bed_c: float | None
    by_hand: bool

    @property
    def counts(self) -> bool:
        return self.set_c is not None and self.held >= HELD and not self.by_hand


@dataclass(frozen=True)
class NightRun:
    wake_on: str
    bedtime_at: datetime
    wake_at: datetime
    parts: tuple[PartRun, ...]
    #: The bedroom's median over lights out to wake. The one outside thing the
    #: house measures that plainly changes a night, and a setting tried only in
    #: a warm week would otherwise take the credit or the blame for the weather.
    room_c: float | None
    #: The part moved on purpose, and by how much, when this was a test night.
    #: None on every night until the evening suggestions exist to make one.
    test_part: str | None = None
    test_offset_c: int | None = None
    rebuilt: bool = False

    def part(self, name: str) -> PartRun | None:
        return next((p for p in self.parts if p.part == name), None)


def build_run(
    plan: NightPlan,
    samples: list[Sample],
    by_hand_at: list[datetime],
    *,
    rebuilt: bool = False,
) -> NightRun:
    """One night, from the plan's parts and the samples recorded across it.

    `by_hand_at` is when anything was set by hand, as the Autopilot screen
    attributes it (autopilot.BY_HAND), so the two can never disagree about which
    nights somebody touched.
    """
    parts = []
    for step in plan.steps:
        inside = [s for s in samples if step.starts_at <= _naive(s.at) < step.ends_at]
        asked = Counter(s.target_c for s in inside if s.target_c is not None)
        set_c = asked.most_common(1)[0][0] if asked else None
        held = round(asked[set_c] / len(inside), 3) if set_c is not None and inside else 0.0
        bed = [_bed(s) for s in inside if _bed(s) is not None]
        parts.append(
            PartRun(
                part=step.stage.value,
                starts_at=step.starts_at,
                ends_at=step.ends_at,
                set_c=set_c,
                held=held,
                bed_c=round(statistics.fmean(bed), 1) if bed else None,
                by_hand=any(step.starts_at <= _naive(at) < step.ends_at for at in by_hand_at),
            )
        )

    room = [
        s.room_c
        for s in samples
        if s.room_c is not None and plan.bedtime_at <= _naive(s.at) < plan.wake_at
    ]
    return NightRun(
        wake_on=plan.wake_at.date().isoformat(),
        bedtime_at=plan.bedtime_at,
        wake_at=plan.wake_at,
        parts=tuple(parts),
        room_c=round(statistics.median(room), 1) if room else None,
        rebuilt=rebuilt,
    )


def _bed(sample: Sample) -> float | None:
    """The return hose, and the outgoing one when that probe is quiet. The same
    rule the live readings and the Health Report use."""
    return sample.return_c if sample.return_c is not None else sample.flow_c


def _naive(at: datetime) -> datetime:
    return at.replace(tzinfo=None)
