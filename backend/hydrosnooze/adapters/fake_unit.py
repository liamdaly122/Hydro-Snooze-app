"""A simulated HydroSnooze HS1001.

This is the most useful thing in the project. It behaves like the real unit,
including all the awkward parts, so every command sequence can be run and checked
before a single infrared photon is emitted. It stays afterwards too, as the way to
test changes without disturbing the bedroom.

Everything it does comes from the manual or from Liam's own testing, recorded in
Part 2 of the build brief. Where something is a guess rather than a confirmed
fact, it carries an ASSUMPTION comment so it can be corrected once the real unit
can be poked.

The behaviour that matters, and that the sequences are built to survive:

- when the unit is off, only the power button does anything
- when the display has gone dark, the first press of any button only wakes it
- for temperature specifically, a second press is then eaten switching the display
  from the current temperature to the target, so from dark it takes three presses
  to change anything
- the display goes dark after five minutes of no input
- while the sleep schedule is running the temperature and timer buttons do nothing
  AND do not wake the display, which is the trap the wake preamble must avoid
- the three cooling speeds cycle and wrap, and can still be changed mid-schedule
- warming is an absolute destination, and cooling from warming lands on Quiet
- inside the setup wizard presses act immediately, and eight seconds of silence
  arms the schedule with whatever temperatures were already saved
- the schedule runs 8h30m and then the unit switches itself off
- the mute button is a toggle and the unit remembers it, so sending it every time
  the unit powers on unmutes it every other night
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..clock import Clock
from ..models import INACTIVITY_CUTOFF, UNIT_SCHEDULE_DURATION, Button, Mode, range_for

#: The manual says fifteen seconds. Liam measured about eight. Take the measured
#: figure, because arming is built to rely on it.
AUTO_APPLY_SECONDS = 8.0

#: The display blanks after five minutes of no input.
DISPLAY_TIMEOUT = timedelta(minutes=5)

#: How long the unit waits for the second press of a power off.
#:
#: The power button is not the toggle it looks like from the front. One press on
#: its own does nothing; two, close together, switch the unit off. Liam confirmed
#: that on the real unit after a night that ended with three presses sent and the
#: bed still running in the morning.
#:
#: ASSUMPTION: the length of the window. The two presses are a confirmed fact,
#: the three seconds is not. It only has to sit between the gap the pair is sent
#: with and the ten seconds the app then waits before asking the plug, and it
#: does. Worth narrowing if a power off ever fails with the display awake.
POWER_OFF_WINDOW = timedelta(seconds=3)


@dataclass
class PressResult:
    """What one button press actually did, for the press log."""

    button: Button
    swallowed: bool
    note: str
    state: str

    def __str__(self) -> str:
        mark = "SWALLOWED" if self.swallowed else ""
        return f"{self.button.value:<10} {mark:<10} {self.note}"


@dataclass
class FakeUnit:
    """The simulated unit. Drive it with `press`, watch it with `describe`."""

    clock: Clock
    #: Part 5 step 7 of the roadmap, still unanswered on the real unit: does the
    #: wizard's auto-apply timeout fire from phase 2 and 3, or only from phase 1?
    #: Flip this to see which sequences stop being self-correcting if it is False.
    auto_apply_from_any_phase: bool = True

    powered: bool = False
    mode: Mode = Mode.QUIET
    #: Each mode remembers its own last temperature independently.
    targets: dict[Mode, int] = field(
        default_factory=lambda: {
            Mode.QUIET: 20,
            Mode.STANDARD: 20,
            Mode.TURBO: 20,
            Mode.WARMING: 30,
        }
    )
    #: The schedule the unit is holding. Survives being unplugged at the wall.
    phase_temps: list[int] = field(default_factory=lambda: [19, 17, 21])

    display_awake_until: datetime | None = None
    #: True once the display has switched from showing the current temperature to
    #: showing the target. Only then do temperature presses change anything.
    adjusting: bool = False

    wizard_phase: int | None = None
    wizard_last_press_at: datetime | None = None
    schedule_armed_at: datetime | None = None
    powered_at: datetime | None = None
    #: For the twelve hour inactivity cutoff, which cannot be disabled.
    last_press_at: datetime | None = None
    #: When power was last pressed while running, for the two-press power off.
    #: A press with nothing recent behind it arms this and does nothing else.
    power_pressed_at: datetime | None = None
    #: Set to model a blaster that answers but transmits nothing. See press().
    deaf: bool = False
    #: The button beep. The unit REMEMBERS this, so the mute button is a toggle,
    #: not a command. Sending it on every power on would unmute every other night.
    muted: bool = False

    # --- Observation ----------------------------------------------------------

    @property
    def target(self) -> int:
        return self.targets[self.mode]

    def schedule_running(self) -> bool:
        now = self.clock.now()
        self._settle(now)
        return self.schedule_armed_at is not None

    def display_dark(self, now: datetime) -> bool:
        return self.display_awake_until is None or now >= self.display_awake_until

    def describe(self) -> str:
        now = self.clock.now()
        self._settle(now)
        if not self.powered:
            return "OFF"
        bits = [self.mode.value, f"target {self.target}C"]
        if self.schedule_armed_at is not None:
            ends = self.schedule_armed_at + UNIT_SCHEDULE_DURATION
            bits.append(f"schedule until {ends:%H:%M}")
        if self.wizard_phase is not None:
            bits.append(f"wizard phase {self.wizard_phase}")
        bits.append("display awake" if not self.display_dark(now) else "display dark")
        if self.muted:
            bits.append("muted")
        return "ON  " + "  ".join(bits)

    def snapshot(self) -> dict[str, object]:
        """For the dev panel in the app."""
        now = self.clock.now()
        self._settle(now)
        return {
            "powered": self.powered,
            "mode": self.mode.value,
            "target_c": self.target if self.powered else None,
            "phase_temps": list(self.phase_temps),
            "display_awake": self.powered and not self.display_dark(now),
            "adjusting": self.adjusting,
            "muted": self.muted,
            "wizard_phase": self.wizard_phase,
            "schedule_running": self.schedule_armed_at is not None,
            "schedule_ends_at": (
                (self.schedule_armed_at + UNIT_SCHEDULE_DURATION).isoformat()
                if self.schedule_armed_at
                else None
            ),
            "watts": round(self.watts(), 1),
        }

    # --- Power draw -----------------------------------------------------------

    def watts(self) -> float:
        """What the plug would read. Derived from what the unit is doing.

        Deliberately not a constant: power_on and power_off verify themselves
        against this reading, so a fake that always returned the same number would
        make those checks pass without testing anything.
        """
        now = self.clock.now()
        self._settle(now)
        if not self.powered:
            return 0.4 + _jitter(0.2)
        if self.mode is Mode.WARMING:
            return 300 + _jitter(18)

        # ASSUMPTION: a pull-down at full draw, then thermostat cycling. Nothing
        # in the manual describes the duty cycle, and it only affects how the
        # history chart looks, not any decision the app makes.
        draw = {Mode.QUIET: 152.0, Mode.STANDARD: 168.0, Mode.TURBO: 184.0}[self.mode]
        elapsed = (now - self.powered_at) if self.powered_at else timedelta()
        if elapsed < timedelta(minutes=20):
            return draw + _jitter(9)
        minute = int(elapsed.total_seconds() // 60)
        return draw + _jitter(9) if minute % 18 < 12 else 31 + _jitter(5)

    # --- The button -----------------------------------------------------------

    def press(self, button: Button) -> PressResult:
        now = self.clock.now()
        self._settle(now)

        # The failure that has no other signature. The board is on the network,
        # the API answers, every button entity is there, every press reports
        # success, and no infrared arrives. It happened on 10 September and the
        # only cure was pulling the USB plug out.
        #
        # Modelled here so the recovery can be tested without unplugging
        # anything. Note it does not even reset the inactivity clock: nothing
        # reached the unit, so nothing about the unit changed.
        if self.deaf:
            return self._result(button, True, "nothing arrived: the unit heard no infrared")

        self.last_press_at = now

        # Inside the wizard presses act immediately. The wake preamble does not
        # apply here, and must not be sent here.
        if self.wizard_phase is not None:
            return self._press_in_wizard(button, now)

        if not self.powered:
            if button is Button.POWER:
                self.powered = True
                self.powered_at = now
                # On takes one press. Only off wants the pair, so nothing is
                # carried across from before it was switched off.
                self.power_pressed_at = None
                self._wake(now)
                return self._result(button, False, "unit powered on")
            return self._result(button, True, "unit is off, only power responds")

        running = self.schedule_armed_at is not None

        # The trap. While a schedule runs these do nothing at all, and crucially
        # they do not wake the display either, so a wake preamble sent here would
        # leave the app believing the unit is awake when it is not.
        if running and button in (Button.TEMP_UP, Button.TEMP_DOWN, Button.TIMER):
            return self._result(
                button, True, "schedule running, ignored without waking the display"
            )

        if self.display_dark(now):
            self._wake(now)
            self.adjusting = False
            return self._result(button, True, "display was dark, this press only woke it")

        self._wake(now)

        if button in (Button.TEMP_UP, Button.TEMP_DOWN):
            if not self.adjusting:
                self.adjusting = True
                return self._result(button, True, "switched the display to the target")
            low, high = range_for(self.mode)
            step = 1 if button is Button.TEMP_UP else -1
            self.targets[self.mode] = max(low, min(high, self.target + step))
            return self._result(button, False, f"target {self.target}C")

        if button is Button.POWER:
            # Two presses, close together. A press on its own arms this and does
            # nothing visible, which is exactly why a press swallowed by a dark
            # display used to leave the unit running: it turned the pair into a
            # single press, and a single press is nothing.
            waiting = self.power_pressed_at
            if waiting is None or now - waiting > POWER_OFF_WINDOW:
                self.power_pressed_at = now
                return self._result(button, False, "one press of power, waiting for the second")
            self.power_pressed_at = None
            self.powered = False
            self.powered_at = None
            self.schedule_armed_at = None
            self.display_awake_until = None
            self.adjusting = False
            return self._result(button, False, "unit powered off")

        if button is Button.COOL:
            # Cycles and wraps. Confirmed by testing, and allowed mid-schedule.
            order = [Mode.QUIET, Mode.STANDARD, Mode.TURBO]
            if self.mode is Mode.WARMING:
                # ASSUMPTION, flagged in the brief as needing confirmation:
                # cooling from warming lands on Quiet. The mode logic depends on
                # this being an absolute destination.
                new = Mode.QUIET
            else:
                new = order[(order.index(self.mode) + 1) % 3]
            was, self.mode = self.mode, new
            self.adjusting = False
            return self._result(button, False, f"mode {was.value} to {new.value}")

        if button is Button.WARM:
            if running:
                return self._result(
                    button, True, "cannot switch between cooling and warming mid-schedule"
                )
            was, self.mode = self.mode, Mode.WARMING
            self.adjusting = False
            return self._result(button, False, f"mode {was.value} to warming")

        if button is Button.SCHEDULE:
            if running:
                self.schedule_armed_at = None
                return self._result(button, False, "left the running schedule")
            self.wizard_phase = 1
            self.wizard_last_press_at = now
            self.adjusting = False
            return self._result(button, False, "entered setup at phase 1")

        if button is Button.TIMER:
            return self._result(button, False, "timer set, not modelled")

        if button is Button.MUTE:
            # A toggle, and the unit remembers it across power cycles. Firing it
            # blind is how you end up unmuting a unit that was already quiet.
            self.muted = not self.muted
            return self._result(button, False, "muted" if self.muted else "UNMUTED")

        return self._result(button, False, "no effect")

    # --- Internals ------------------------------------------------------------

    def _press_in_wizard(self, button: Button, now: datetime) -> PressResult:
        self.wizard_last_press_at = now
        self._wake(now)
        phase = self.wizard_phase or 1

        if button in (Button.TEMP_UP, Button.TEMP_DOWN):
            low, high = range_for(self.mode)
            step = 1 if button is Button.TEMP_UP else -1
            idx = phase - 1
            self.phase_temps[idx] = max(low, min(high, self.phase_temps[idx] + step))
            return self._result(button, False, f"phase {phase} = {self.phase_temps[idx]}C")

        if button is Button.SCHEDULE:
            if phase < 3:
                self.wizard_phase = phase + 1
                return self._result(button, False, f"advanced to phase {phase + 1}")
            self._arm(now)
            return self._result(button, False, "left setup, schedule armed")

        if button is Button.POWER:
            self.powered = False
            self.powered_at = None
            self.wizard_phase = None
            self.display_awake_until = None
            return self._result(button, False, "unit powered off, setup abandoned")

        # ASSUMPTION: mode buttons inside the wizard do nothing. Untested.
        return self._result(button, True, "ignored inside the setup wizard")

    def _settle(self, now: datetime) -> None:
        """Resolve anything the unit would have done on its own since last time."""
        # Twelve hours with no button press and it switches itself off. Cannot be
        # disabled. Every stage transition resets it, so across a normal night it
        # never fires, but a night that loses its scheduler would end here.
        # Powering on counts as activity, so the clock starts from whichever
        # happened last.
        last_touched = self.last_press_at or self.powered_at
        if self.powered and last_touched is not None and now - last_touched >= INACTIVITY_CUTOFF:
            self.powered = False
            self.powered_at = None
            self.schedule_armed_at = None
            self.display_awake_until = None

        if self.wizard_phase is not None and self.wizard_last_press_at is not None:
            idle = (now - self.wizard_last_press_at).total_seconds()
            if idle >= AUTO_APPLY_SECONDS:
                if self.wizard_phase == 1 or self.auto_apply_from_any_phase:
                    self._arm(now)
                else:
                    # The unanswered question resolved the unhelpful way: setup
                    # times out without arming anything.
                    self.wizard_phase = None
                    self.wizard_last_press_at = None

        if (
            self.schedule_armed_at is not None
            and now >= self.schedule_armed_at + UNIT_SCHEDULE_DURATION
        ):
            self.schedule_armed_at = None
            self.powered = False
            self.powered_at = None
            self.display_awake_until = None

    def _arm(self, now: datetime) -> None:
        self.wizard_phase = None
        self.wizard_last_press_at = None
        self.schedule_armed_at = now
        # The schedule runs on its own saved phase temperatures, not the current
        # target, which is why the wake preamble's stray presses do not matter.
        self.targets[self.mode] = self.phase_temps[0]

    def _wake(self, now: datetime) -> None:
        self.display_awake_until = now + DISPLAY_TIMEOUT

    def _result(self, button: Button, swallowed: bool, note: str) -> PressResult:
        return PressResult(button=button, swallowed=swallowed, note=note, state=self.describe())


def _jitter(spread: float) -> float:
    return random.uniform(-spread, spread)
