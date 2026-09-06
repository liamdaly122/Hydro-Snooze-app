"""The button recipes, transcribed from Part 3 of the build brief.

Two ideas hold this together.

**Rail and count.** The unit cannot be read, so its temperature is never tracked
incrementally. Every time a known temperature is needed, one is forced: press down
enough times to pin the unit at the mode's minimum, then count up to the target.
Twenty five presses in cooling, thirty five in warming. That makes every
temperature command correct from any starting state, including after the physical
remote has been used.

**The wake preamble.** The display goes dark after five minutes, and the first
press after that is swallowed waking it, with a second swallowed switching to the
target display. The app has no way to know whether the display is awake, so every
sequence begins with two discarded `temp_down` presses.

`temp_down` is the deliberate choice for the throwaway presses. If a preamble ever
lands somewhere unintended the failure is a slightly colder bed rather than a
hotter one, and in cooling it simply bottoms out at 15C. Never `temp_up`, never
`power`, never `schedule`.

The preamble must never be sent during an active schedule, where temperature
presses are swallowed *without* waking the display, and it is never sent inside the
setup wizard, where presses act immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .adapters.base import PowerMonitor, Transmitter
from .clock import Clock
from .config import Settings
from .events import EventLog
from .models import Button, Mode, rail_count, range_for

#: Warn if any single gap inside the setup wizard exceeds this. The wizard gives
#: up after about eight seconds of silence and arms with whatever it already had,
#: which is a safe failure but not the one that was asked for.
WIZARD_GAP_WARNING_S = 6.0


class CommandFailed(RuntimeError):
    """A sequence could not be verified. State goes to unknown, no blind retry."""


@dataclass
class WriteProgress:
    phase: str
    presses_sent: int
    presses_total: int
    message: str


class ProgressSink(Protocol):
    def __call__(self, progress: WriteProgress) -> None: ...


class Commands:
    """Every sequence the app can run against the unit."""

    def __init__(
        self,
        transmitter: Transmitter,
        power: PowerMonitor,
        clock: Clock,
        settings: Settings,
        events: EventLog,
    ) -> None:
        self.tx = transmitter
        self.power = power
        self.clock = clock
        self.settings = settings
        self.events = events

    # --- Building blocks ------------------------------------------------------

    async def _press(self, button: Button, note: str = "", gap: float | None = None) -> float:
        """Send one press and wait the gap. Returns how long the gap was."""
        before = self.clock.now()
        await self.tx.press(button, note)
        wait = self.settings.command_gap_s if gap is None else gap
        if wait:
            await self.clock.sleep(wait)
        return (self.clock.now() - before).total_seconds()

    def _banner(self, text: str) -> None:
        banner = getattr(self.tx, "banner", None)
        if callable(banner):
            banner(text)

    async def wake(self) -> None:
        """Two discarded presses, so the next one is guaranteed to land.

        Never call this during an active schedule, and never inside the wizard.
        """
        await self._press(Button.TEMP_DOWN, "wake 1/2")
        await self._press(Button.TEMP_DOWN, "wake 2/2")

    # --- Sequences ------------------------------------------------------------

    async def set_mode(self, mode: Mode) -> None:
        """Force a known mode. Precondition: the unit is on.

        Warming is an absolute destination, so the route to any mode is: press
        warm, then press cool the right number of times. Cooling from warming
        lands on Quiet, then the cycle is Quiet, Standard, Turbo.
        """
        self._banner(f"set_mode({mode.value})")
        await self.wake()
        await self._press(Button.WARM, "absolute")

        if mode is not Mode.WARMING:
            steps = {Mode.QUIET: 1, Mode.STANDARD: 2, Mode.TURBO: 3}[mode]
            for i in range(steps):
                await self._press(Button.COOL, f"cool {i + 1}/{steps}")

        await self.clock.sleep(self.settings.save_wait_s)
        self.events.info("mode", f"Set mode to {mode.value} via warm then cool")

    async def set_temperature(self, target_c: int, mode: Mode) -> None:
        """Rail to the mode's minimum, then count up.

        Preconditions: the unit is on, and no schedule is running, because
        temperature adjustment is disabled there.
        """
        low, high = range_for(mode)
        if not low <= target_c <= high:
            raise CommandFailed(f"{target_c}C is outside {mode.value}'s range of {low} to {high}")
        if target_c > self.settings.max_temperature_c:
            raise CommandFailed(
                f"{target_c}C is above the {self.settings.max_temperature_c}C safety cap"
            )

        self._banner(f"set_temperature({target_c}, {mode.value})")
        await self.wake()

        rail = rail_count(mode)
        for i in range(rail):
            await self._press(Button.TEMP_DOWN, f"rail {i + 1}/{rail}")

        ups = target_c - low
        for i in range(ups):
            await self._press(Button.TEMP_UP, f"up {i + 1}/{ups}")

        # The unit needs about three seconds to commit the change. Powering off
        # before that loses it.
        await self.clock.sleep(self.settings.save_wait_s)
        self.events.info(
            "temperature", f"Railed to {low}C then counted up to {target_c}C ({rail} + {ups})"
        )

    async def power_on(self) -> None:
        self._banner("power_on()")
        watts = await self.power.read_watts()
        if watts is not None and watts >= self.settings.off_threshold_w:
            self.events.info("power", f"Already on, plug reads {watts:.1f} W")
            return

        await self._press(Button.POWER, "on")
        if await self._settled_on():
            return

        # One retry, then stop. Never retry a whole sequence blindly.
        await self._press(Button.POWER, "on, retry")
        if await self._settled_on():
            self.events.warning("power", "Powered on, but it took two presses")
            return

        raise CommandFailed("Pressed power twice and the plug still reads off")

    async def power_off(self) -> None:
        """The display may be asleep, in which case the first press only wakes it.

        So send two. If the display was already awake those two cancel out and the
        unit is still on, which the verification below catches and corrects with a
        third.
        """
        self._banner("power_off()")
        watts = await self.power.read_watts()
        if watts is not None and watts < self.settings.off_threshold_w:
            self.events.info("power", f"Already off, plug reads {watts:.1f} W")
            return

        await self._press(Button.POWER, "off 1/2", gap=0.6)
        await self._press(Button.POWER, "off 2/2", gap=0)
        if await self._settled_off():
            return

        await self._press(Button.POWER, "off, the two cancelled out", gap=0)
        if await self._settled_off():
            self.events.warning("power", "Powered off, but the first two presses cancelled out")
            return

        raise CommandFailed("Pressed power three times and the plug still reads on")

    async def arm_schedule(self) -> None:
        """The routine nightly operation, and the one that must not fail quietly.

        It runs about half an hour after the pre-cool step, by which time the
        display has gone dark again. Without the preamble a single schedule press
        onto a dark display only wakes it: the app would report success, nothing
        would be armed, and the first anyone would know is at 3am.

        Self-correcting if a press is dropped: landing in phase 1 or phase 2 makes
        no difference, because doing nothing for eight seconds applies the saved
        temperatures either way.
        """
        self._banner("arm_schedule()")
        await self.wake()
        await self.clock.sleep(0.5)
        await self._press(Button.SCHEDULE, "enter setup", gap=0)
        await self.clock.sleep(self.settings.arm_wait_s)
        self.events.info(
            "schedule_arm",
            f"Armed the schedule, {self.settings.arm_wait_s}s auto-apply wait",
        )

    async def write_schedule(
        self,
        phase_temps: tuple[int, int, int],
        mode: Mode,
        on_progress: ProgressSink | None = None,
    ) -> None:
        """Walk the unit through its setup wizard. The risky one.

        Never run automatically. It has to be triggered by an explicit action in
        the app, with an on-screen message telling Liam to watch the unit, because
        nothing in software can read back what was written.
        """
        low, _ = range_for(mode)
        for temp in phase_temps:
            if temp > self.settings.max_temperature_c:
                raise CommandFailed(
                    f"{temp}C is above the {self.settings.max_temperature_c}C safety cap"
                )

        mode_presses = 2 + 1 + (0 if mode is Mode.WARMING else {Mode.QUIET: 1, Mode.STANDARD: 2, Mode.TURBO: 3}[mode])
        per_phase = [rail_count(mode) + (t - low) for t in phase_temps]
        total = mode_presses + sum(per_phase) + 4
        sent = 0

        def report(phase: str, message: str) -> None:
            if on_progress:
                on_progress(WriteProgress(phase, sent, total, message))

        self._banner(f"write_schedule({phase_temps}, {mode.value})")
        report("mode", "Setting the mode")

        # The mode cannot be changed once inside the schedule, so it goes first.
        await self.set_mode(mode)
        sent += mode_presses

        slow_phases: list[int] = []

        for index, temp in enumerate(phase_temps, start=1):
            await self._press(Button.SCHEDULE, f"-> phase {index}", gap=0.5)
            sent += 1
            report(f"phase{index}", f"Writing phase {index}")

            # Same rail and count, but no preamble and no trailing save wait:
            # inside the wizard presses act immediately.
            worst_gap = 0.0
            rail = rail_count(mode)
            for i in range(rail):
                worst_gap = max(worst_gap, await self._press(Button.TEMP_DOWN, f"rail {i + 1}/{rail}"))
                sent += 1
                if i % 5 == 0:
                    report(f"phase{index}", f"Writing phase {index}")
            ups = temp - low
            for i in range(ups):
                worst_gap = max(worst_gap, await self._press(Button.TEMP_UP, f"up {i + 1}/{ups}"))
                sent += 1
            report(f"phase{index}", f"Phase {index} set to {temp}C")

            if worst_gap > WIZARD_GAP_WARNING_S:
                slow_phases.append(index)

        await self._press(Button.SCHEDULE, "exit, armed", gap=0)
        sent += 1
        report("done", "Schedule written")

        if slow_phases:
            # The wizard gives up after about eight seconds of silence and arms
            # with the previously saved temperatures. Safe, but not what was asked
            # for, and impossible to detect any other way.
            self.events.warning(
                "schedule_write",
                f"Phases {slow_phases} had gaps over {WIZARD_GAP_WARNING_S}s between presses. "
                "The wizard may have timed out and kept its old temperatures.",
            )
        self.events.info(
            "schedule_write",
            f"Wrote {phase_temps[0]}/{phase_temps[1]}/{phase_temps[2]}C in {mode.value}, "
            f"{total} presses. Nothing can verify this from here.",
        )

    async def set_cooling_speed(self, target: Mode, current: Mode) -> None:
        """Change cooling speed during a running schedule.

        The only mode change the unit allows mid-schedule. There is no preamble,
        because temperature presses are swallowed without waking the display
        there, so this only works while the display is already awake. Send it
        within seconds of a previous press, never as a separate scheduled job.
        """
        if target is Mode.WARMING or current is Mode.WARMING:
            raise CommandFailed("Cooling and warming cannot be switched mid-schedule")
        order = [Mode.QUIET, Mode.STANDARD, Mode.TURBO]
        steps = (order.index(target) - order.index(current)) % 3
        if steps == 0:
            return
        self._banner(f"set_cooling_speed({current.value} -> {target.value})")
        for i in range(steps):
            await self._press(Button.COOL, f"cool {i + 1}/{steps}")
        self.events.info("mode", f"Dropped from {current.value} to {target.value} mid-schedule")

    # --- Verification ---------------------------------------------------------

    async def _settled_on(self) -> bool:
        await self.clock.sleep(self.settings.power_settle_s)
        watts = await self.power.read_watts()
        if watts is None:
            raise CommandFailed("Plug unreachable, cannot confirm the unit powered on")
        return watts >= self.settings.off_threshold_w

    async def _settled_off(self) -> bool:
        await self.clock.sleep(self.settings.power_settle_s)
        watts = await self.power.read_watts()
        if watts is None:
            raise CommandFailed("Plug unreachable, cannot confirm the unit powered off")
        return watts < self.settings.off_threshold_w
