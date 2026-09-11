"""A plug that reports what the simulated unit is actually drawing, slowly.

Deliberately wired to the same FakeUnit the transmitter drives, rather than
returning a fixed number. power_on and power_off verify themselves against this
reading, so a fake that always said the same thing would make those checks pass
without testing anything at all.

It is also deliberately late, which it was not until 11 September. The real
Shelly does not report a change of draw for the better part of a minute, and the
fake answering instantly is the reason a year of tests agreed with an app that
could not switch the bed off. Every test asked the plug the moment the presses
went out, got the truth, and passed. Every real morning asked the plug ten
seconds after the presses, got the draw from before them, decided nothing had
landed, and sent more presses.

A simulator that is easier than the thing it simulates is worse than no
simulator, because it manufactures confidence. So this one lags.
"""

from __future__ import annotations

from datetime import datetime

from ..clock import Clock
from .fake_unit import FakeUnit

#: How long the plug takes to report that the unit switched on or off.
#:
#: Liam measured "at least 30 seconds" on the real one on 11 September, watching
#: a unit that had visibly switched off still reading as running. Thirty five
#: here so a test written against the measurement does not sit exactly on the
#: boundary of it.
REPORTING_LAG_S = 35.0


class FakePowerMonitor:
    def __init__(
        self, unit: FakeUnit, clock: Clock | None = None, lag_s: float = REPORTING_LAG_S
    ) -> None:
        self.unit = unit
        self.clock = clock
        self.lag_s = lag_s if clock is not None else 0.0
        #: Set this to simulate the plug being unreachable, which is the case the
        #: app has to report as "unknown" rather than guessing at.
        self.offline = False
        self._was_on = unit.powered
        self._changed_at: datetime | None = None
        self._reported: float | None = None

    async def read_watts(self) -> float | None:
        """The draw, as the plug currently believes it to be.

        Holding the previous reading rather than interpolating towards the new
        one, because that is what the real plug does from the app's point of
        view: `apower` reads like a unit that is still running right up until it
        reads like one that is not.
        """
        if self.offline:
            return None

        live = round(self.unit.watts(), 1)
        if self.lag_s <= 0 or self.clock is None:
            return live

        now = self.clock.now()
        if self.unit.powered != self._was_on:
            self._was_on = self.unit.powered
            self._changed_at = now

        stale = (
            self._changed_at is not None
            and self._reported is not None
            and (now - self._changed_at).total_seconds() < self.lag_s
        )
        if stale:
            return self._reported

        self._reported = live
        return live

    async def reachable(self) -> bool:
        return True

    async def close(self) -> None:
        return None
