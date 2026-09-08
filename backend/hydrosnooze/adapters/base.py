"""The two interfaces that make the hardware swappable.

Everything above these lines is written once. Below them there are two
implementations of each: one that talks to a simulated unit, and one that talks to
real hardware. Choosing between them is `HS_TRANSMITTER` and `HS_POWER_MONITOR` in
the environment, and nothing else in the project knows or cares which is in use.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Button


@runtime_checkable
class Transmitter(Protocol):
    """Sends one remote button press."""

    async def press(self, button: Button, note: str = "") -> None:
        """Send `button`. `note` is context for the press log, e.g. "wake 1/2"."""

    async def reachable(self) -> bool:
        """Whether the blaster can be talked to right now.

        Presses are hours apart, so without asking, a blaster that fell off the
        Wi-Fi at midnight would look perfectly fine until the stage that needed
        it. Infrared is one way, so this only says the board is there, not that
        the unit received anything.
        """

    async def close(self) -> None: ...


@runtime_checkable
class PowerMonitor(Protocol):
    """Reads the smart plug. The only genuinely observed signal in the project."""

    async def read_watts(self) -> float | None:
        """Current draw, or None if the plug could not be reached."""

    async def reachable(self) -> bool:
        """Whether the plug answered. Every read already asks, so this is free."""

    async def close(self) -> None: ...
