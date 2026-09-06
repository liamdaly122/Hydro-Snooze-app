from __future__ import annotations

from datetime import datetime

import pytest

from hydrosnooze.adapters import build_adapters
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.events import EventLog
from hydrosnooze.sequences import Commands


@pytest.fixture
def clock() -> VirtualClock:
    # A Monday evening, half an hour before the schedule would arm.
    return VirtualClock(datetime(2026, 9, 7, 21, 30))


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def rig(clock, settings):
    """Transmitter, plug, simulated unit and the commands that drive them."""
    tx, power, unit = build_adapters(settings, clock, echo=False)
    commands = Commands(tx, power, clock, settings, EventLog(clock))

    class Rig:
        def __init__(self) -> None:
            self.tx = tx
            self.power = power
            self.unit = unit
            self.commands = commands
            self.clock = clock

        def unit_on(self, mode=None, target=None, *, display_dark=True):
            """Put the unit in a plausible starting state."""
            from hydrosnooze.models import Mode

            self.unit.powered = True
            self.unit.powered_at = clock.now()
            if mode is not None:
                self.unit.mode = mode
            if target is not None:
                self.unit.targets[self.unit.mode] = target
            if display_dark:
                self.unit.display_awake_until = None
                self.unit.adjusting = False
            else:
                self.unit.display_awake_until = clock.now().replace(microsecond=0)
                self.unit._wake(clock.now())
            _ = Mode
            return self.unit

    return Rig()
