"""A plug that reports what the simulated unit is actually drawing.

Deliberately wired to the same FakeUnit the transmitter drives, rather than
returning a fixed number. power_on and power_off verify themselves against this
reading, so a fake that always said the same thing would make those checks pass
without testing anything at all.
"""

from __future__ import annotations

from .fake_unit import FakeUnit


class FakePowerMonitor:
    def __init__(self, unit: FakeUnit) -> None:
        self.unit = unit
        #: Set this to simulate the plug being unreachable, which is the case the
        #: app has to report as "unknown" rather than guessing at.
        self.offline = False

    async def read_watts(self) -> float | None:
        if self.offline:
            return None
        return round(self.unit.watts(), 1)

    async def reachable(self) -> bool:
        return True

    async def close(self) -> None:
        return None
