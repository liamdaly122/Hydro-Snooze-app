"""A transmitter that prints what it would have sent.

This is the debugging tool the whole project is built around. Every press is
printed with what the simulated unit did about it, so a sequence can be read
press by press and checked before any of it is trusted with real infrared:

    21:30:00.0  -> temp_down   wake 1/2      SWALLOWED  display was dark
    21:30:00.3  -> temp_down   wake 2/2      SWALLOWED  switched to the target
    21:30:00.6  -> temp_down   rail 1/25                target 19C

It keeps the last few hundred lines so the app's dev panel can show them too.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from ..clock import Clock
from ..models import Button
from .fake_unit import FakeUnit

LOG_LINES = 400


class FakeTransmitter:
    def __init__(
        self,
        unit: FakeUnit,
        clock: Clock,
        *,
        echo: bool = True,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self.unit = unit
        self.clock = clock
        self.echo = echo
        self.on_line = on_line
        self.lines: deque[str] = deque(maxlen=LOG_LINES)
        self.presses_sent = 0

    async def press(self, button: Button, note: str = "") -> None:
        result = self.unit.press(button)
        self.presses_sent += 1
        stamp = f"{self.clock.now():%H:%M:%S.%f}"[:-5]
        mark = "SWALLOWED" if result.swallowed else "         "
        self._emit(f"{stamp}  -> {button.value:<10} {note:<13} {mark}  {result.note}")

    def banner(self, text: str) -> None:
        """A header line for the start or end of a sequence."""
        stamp = f"{self.clock.now():%H:%M:%S.%f}"[:-5]
        self._emit(f"{stamp}  {text}")

    def _emit(self, line: str) -> None:
        self.lines.append(line)
        if self.echo:
            print(line, flush=True)
        if self.on_line:
            self.on_line(line)

    async def close(self) -> None:
        return None
