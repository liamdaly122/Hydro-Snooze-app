"""Time, injectable.

Every wait in this project goes through a Clock. In production that is the real
one. In testing it is a simulated one, which is what makes it possible to jump to
21:29 and watch a whole evening happen in a few seconds rather than waiting until
bedtime.

The simulated clock runs time at a multiple of real time rather than just adding
an offset. That matters: the unit's own timers, the five minute display blackout
and the eight second auto-arm, are measured against this clock too. If sleeps were
compressed but the clock was not, the fake unit would never time anything out and
the simulation would quietly stop resembling the real thing.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, real or simulated."""

    async def sleep(self, seconds: float) -> None:
        """Wait for `seconds` of clock time."""


class RealClock:
    """Wall time. What runs on the Pi."""

    def now(self) -> datetime:
        return datetime.now()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimClock:
    """Simulated time, running at `speed` times real time.

    At speed 1 it behaves like the real clock but can be jumped. At speed 60 a
    300ms gap between presses takes 5ms of real time and still advances the
    simulation by the full 300ms, so a nightly routine plays out in seconds while
    every duration inside it stays honest.
    """

    def __init__(self, start: datetime | None = None, speed: float = 1.0) -> None:
        self._sim = start or datetime.now()
        self._wall = _time.monotonic()
        self._speed = max(speed, 1e-6)

    @property
    def speed(self) -> float:
        return self._speed

    def now(self) -> datetime:
        elapsed = _time.monotonic() - self._wall
        return self._sim + timedelta(seconds=elapsed * self._speed)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self._speed)

    def jump_to(self, target: datetime) -> None:
        """Move the simulation to a specific moment, e.g. 21:29 this evening."""
        self._rebase()
        self._sim = target

    def advance(self, delta: timedelta) -> None:
        self._rebase()
        self._sim += delta

    def set_speed(self, speed: float) -> None:
        self._rebase()
        self._speed = max(speed, 1e-6)

    def _rebase(self) -> None:
        """Pin the simulation to now before changing how time flows."""
        self._sim = self.now()
        self._wall = _time.monotonic()


#: Above this, the real time spent doing the work gets multiplied into the
#: simulation badly enough to distort it: a 300ms gap between presses can stretch
#: past the wizard's eight second timeout and the simulation stops resembling the
#: unit. Watching an evening play out wants about 60. Tests want VirtualClock.
MAX_SIM_SPEED = 120.0


class VirtualClock:
    """Time that advances only when something sleeps, and instantly.

    For tests. A sequence of ninety presses at 300ms apart runs in microseconds
    and still records exactly 27 seconds of elapsed time, so timing assertions are
    deterministic and the real time spent executing never leaks into the
    simulation.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 7, 21, 30)

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        # Yield so other tasks still get a turn, without any real delay.
        await asyncio.sleep(0)

    def jump_to(self, target: datetime) -> None:
        self._now = target

    def advance(self, delta: timedelta) -> None:
        self._now += delta
