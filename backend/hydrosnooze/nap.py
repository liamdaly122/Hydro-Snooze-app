"""A nap: the bed to one temperature, held for a while, then off.

Not a night. A night is planned in advance from the schedule and runs on the
scheduler's clock; a nap starts when somebody presses Start, and its length is
counted from the moment the bed gets there, not from the press. That moment is
not known in advance, so a nap is a small state of its own rather than a
NightPlan, watched on the sampling beat and ended on the tick.

**When the bed has got there.** The same evidence getting a bed ready uses, in
the same order of trust, with one addition at the front:

    bed       the hose probe on the way back reads within READY_BAND_C of the
              number, on the side it was coming from
    probes    the probes saw heat move and have now seen it stop: the bed has
              finished taking heat, even if it settles short of the number,
              which a warming bed does by a couple of degrees
    plug      no probes, and the unit has dropped from working to idle
    estimate  none of the above by GIVE_UP_AFTER past the expected time, so the
              timer starts anyway rather than running the unit for ever

**Not a night, in every sense.** Nothing of it goes on the scoreboard, into
Trends or into a morning report, because none of it is a night.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .models import Activity, Mode

#: How close the bed has to get to count as there, by the probe alone. The two
#: hose probes sit a quarter of a degree apart on a settled bed.
READY_BAND_C = 0.5

#: Past the expected ready time by this, or half the expected wait if that is
#: longer, and the timer starts without being told. A nap that never started
#: its own clock would never end.
GIVE_UP_AFTER = timedelta(minutes=15)

#: Lengths a nap may be: five minutes to three hours, in fives.
SHORTEST_MINUTES = 5
LONGEST_MINUTES = 180

#: How long after starting before a reading of the plug at off means the unit
#: really is off. The plug reports half a minute late, so straight after the
#: presses it can still be reading the unit before them.
PLUG_LAG = timedelta(minutes=3)


@dataclass(frozen=True)
class Nap:
    temp_c: int
    minutes: int
    mode: Mode
    started_at: datetime
    #: When the bed is expected to get there: the learned head start, or the
    #: estimate until there is one. The start itself when it is there already.
    expect_ready_at: datetime
    #: When it got there, which is when the length starts counting.
    ready_at: datetime | None = None
    #: Which evidence said so: bed, probes, plug or estimate.
    ready_by: str | None = None

    @property
    def ends_at(self) -> datetime | None:
        if self.ready_at is None:
            return None
        return self.ready_at + timedelta(minutes=self.minutes)

    @property
    def give_up_at(self) -> datetime:
        wait = self.expect_ready_at - self.started_at
        return self.expect_ready_at + max(GIVE_UP_AFTER, wait / 2)

    def ready(self, at: datetime, by: str) -> Nap:
        return replace(self, ready_at=at, ready_by=by)


def arrived(
    nap: Nap,
    now: datetime,
    *,
    bed_c: float | None,
    moving_c: float | None,
    worked: bool,
    activity: Activity | None,
    settle_after_s: int,
    settled_c: float,
) -> str | None:
    """Which evidence says the bed has got there, or None while it has not.

    `worked` is whether the probes have seen heat move since the nap started;
    only then can a closed gap between the hoses mean the bed has stopped
    taking heat rather than that nothing has started yet.
    """
    if nap.ready_at is not None:
        return None
    if bed_c is not None:
        if nap.mode is Mode.WARMING and bed_c >= nap.temp_c - READY_BAND_C:
            return "bed"
        if nap.mode.is_cooling and bed_c <= nap.temp_c + READY_BAND_C:
            return "bed"
    elapsed = (now - nap.started_at).total_seconds()
    if moving_c is not None and worked:
        if abs(moving_c) <= settled_c and elapsed >= settle_after_s:
            return "probes"
    elif moving_c is None and activity is Activity.IDLE and elapsed >= settle_after_s:
        return "plug"
    if now >= nap.give_up_at:
        return "estimate"
    return None
