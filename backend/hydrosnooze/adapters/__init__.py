"""Choosing which hardware to talk to.

This module is the entire swap. `HS_TRANSMITTER=fake` today, `esphome` once the
blaster arrives, and nothing above this line changes.
"""

from __future__ import annotations

from ..clock import Clock
from ..config import Settings
from .base import PowerMonitor, Transmitter
from .fake_power import FakePowerMonitor
from .fake_transmitter import FakeTransmitter
from .fake_unit import FakeUnit

__all__ = [
    "PowerMonitor",
    "Transmitter",
    "FakeUnit",
    "FakeTransmitter",
    "FakePowerMonitor",
    "build_adapters",
]


def build_adapters(
    settings: Settings, clock: Clock, *, echo: bool = True
) -> tuple[Transmitter, PowerMonitor, FakeUnit | None]:
    """Return the transmitter, the power monitor, and the simulated unit if any.

    The simulated unit comes back so the dev endpoints can show its state and the
    tests can assert against it. In production it is None.
    """
    unit: FakeUnit | None = None

    if settings.transmitter == "fake":
        unit = FakeUnit(
            clock=clock, auto_apply_from_any_phase=settings.sim_auto_apply_from_any_phase
        )
        transmitter: Transmitter = FakeTransmitter(unit, clock, echo=echo)
    else:
        from .esphome import EsphomeTransmitter

        transmitter = EsphomeTransmitter(
            settings.esphome_host, settings.esphome_port, settings.esphome_encryption_key
        )

    if settings.power_monitor == "fake":
        if unit is None:
            raise ValueError(
                "HS_POWER_MONITOR=fake needs HS_TRANSMITTER=fake: the fake plug reads "
                "the simulated unit, and there is no simulated unit to read."
            )
        # The clock is what lets it be as slow as the real plug.
        power: PowerMonitor = FakePowerMonitor(unit, clock)
    else:
        from .shelly import ShellyPowerMonitor

        power = ShellyPowerMonitor(settings.shelly_host)

    return transmitter, power, unit
