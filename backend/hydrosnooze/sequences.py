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

The unit's own Smart Sleep Schedule is never armed, so the case where temperature
presses are swallowed without waking the display cannot arise. That was the
sharpest edge in the original design, and dropping their scheduler removes it.
"""

from __future__ import annotations

from .adapters.base import PowerMonitor, Transmitter
from .clock import Clock
from .config import Settings
from .events import EventLog
from .models import Button, Mode, rail_count, range_for


class CommandFailed(RuntimeError):
    """A sequence could not be verified. State goes to unknown, no blind retry."""


class NotLanding(CommandFailed):
    """Presses went out, the plug answered, and nothing about the unit changed.

    The one moment this project can prove infrared is not arriving. Everywhere
    else a press is fire and forget: the temperature has no readback, so a press
    that vanished looks exactly like one that worked. Power is different, because
    the plug is watching, and a unit that was off and stays off through two
    presses of power did not receive them.

    Worth its own type rather than a message to match on, because it is the one
    failure with a specific remedy: restart the board and try once more.
    """


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
        """Send one press and wait the gap. Returns how long the gap was.

        Every failure to send becomes a CommandFailed here, whatever the
        transmitter raised. That is the one thing the layer above catches, and a
        press that did not go out is a command that did not work regardless of
        which library named the error.

        Catching broadly is on purpose and was learned the hard way: an
        aioesphomeapi APIConnectionError escaped this path, went past the
        scheduler's handler for CommandFailed, and left the nightly loop retrying
        the same stage every second until morning.
        """
        before = self.clock.now()
        try:
            await self.tx.press(button, note)
        except CommandFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CommandFailed(f"Could not send {button.value}: {exc}") from exc
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
        """One press, then wait for the plug to agree.

        A unit that is off answers nothing but power, and answers it with one
        press, so there is no display to wake and no gesture to get wrong. All
        the care here is in the waiting: the plug takes the better part of a
        minute to report a jump from standby to a running unit, and a second
        press sent inside that lag lands on a unit that is already on and with a
        lit display, which switches it straight back off.
        """
        self._banner("power_on()")
        watts = await self.power.read_watts()
        if watts is not None and watts >= self.settings.off_threshold_w:
            self.events.info("power", f"Already on, plug reads {watts:.1f} W")
            return

        await self._press(Button.POWER, "on")
        if await self._confirm(off=False):
            return

        raise NotLanding("Pressed power and the plug still reads off two minutes later")

    async def press_power(self) -> None:
        """One press of power. No wake, no check, no retry, no second thoughts.

        Everything else in this file is a sequence built to be safe when nobody
        is watching: it reads the plug first, decides whether there is anything
        to do, sends what is needed and confirms it landed. That is right for
        three in the morning and wrong for someone standing in front of the bed
        with the app in their hand, who wants the button to do what the button on
        the remote does.

        So this is the remote's button. It sends one code and stops. What the
        unit makes of it is the unit's business, and the person who pressed it is
        looking straight at the answer.

        The scheduled power off does not come through here. Nobody is watching at
        the wake time, so that one keeps its wake preamble, its pair of presses
        and its confirmation against the plug.
        """
        self._banner("press_power()")
        await self._press(Button.POWER, "one press, by hand")
        self.events.info("power", "Sent one press of power")

    async def power_off(self) -> None:
        """One gesture, then wait long enough to actually know whether it worked.

        Rewritten on 11 September, after a morning where the app sent eight
        presses of power, restarted the board twice, and left the bed running.

        Two things were wrong, and they fed each other.

        **It pressed power one time too many.** The unit's own behaviour is
        exactly two steps: a press wakes the display, and the next press switches
        the unit off. This file used to send a wake preamble of two `temp_down`
        presses and then a *pair* of power presses, which on an already lit
        display is one press to switch off and a second, six tenths of a second
        later, to switch straight back on. It ended on every time. The old
        comment here described power as needing a pair, and that was a guess made
        to explain an earlier failure. It was wrong.

        **And the plug is slow.** A Shelly does not report a change of draw for
        the better part of a minute. The check afterwards waited ten seconds, saw
        a unit that was still "drawing", concluded nothing had landed, and sent
        the gesture again. So the correction was the thing that broke it, every
        time, and then the board was restarted for a fault that was never there.

        So: as few presses as the unit needs, and then real patience.
        """
        self._banner("power_off()")
        watts = await self.power.read_watts()
        if watts is not None and watts < self.settings.off_threshold_w:
            self.events.info("power", f"Already off, plug reads {watts:.1f} W")
            return

        await self._off_gesture()
        if await self._confirm(off=True):
            return

        raise NotLanding(
            f"Pressed power and the plug still reads on "
            f"{self.settings.power_confirm_s}s later"
        )

    async def _off_gesture(self) -> None:
        """Wake the display, then the one press that switches the unit off.

        Liam works the remote by hand as two presses of power: "one to turn the
        display on and then one to turn off the unit". That is two steps, not two
        presses, and only the second step has to be power. The first is just
        waking the display, and this file has had a safer way to do that since the
        beginning.

        So the wake preamble stays and the second press of power goes. `temp_down`
        cannot switch anything on, which is the entire reason the preamble is
        `temp_down` everywhere else in this file. A press of power can, and a
        spare one sent at a unit that has just switched off is precisely what was
        leaving the bed running.

        Three presses where the hand sends two, and deterministic where guessing
        is not. Working out whether the display is already lit was the obvious
        alternative and it is not knowable: the physical remote is invisible from
        here, so a guess is wrong exactly when someone has been at the unit, and
        being wrong costs a bed that switches off and straight back on. Two
        harmless presses buy certainty from every starting state.
        """
        await self.wake()
        await self._press(Button.POWER, "switches the unit off", gap=0)

    async def mute(self) -> None:
        """Silence the button beep.

        Not decorative any more. Driving the night live means roughly thirty
        presses at each stage boundary, and the unit beeps on every one of them,
        at two in the morning, next to a bed.
        """
        self._banner("mute()")
        await self.wake()
        await self._press(Button.MUTE, "silence")
        self.events.info("mute", "Muted the unit's button beep")

    # --- Verification ---------------------------------------------------------

    async def _confirm(self, *, off: bool) -> bool:
        """Keep asking the plug until it agrees, or until patience runs out.

        This replaced a single check ten seconds after the presses, which was the
        other half of the morning described in power_off. Ten seconds is inside
        the plug's own reporting lag, so the answer it gave was not "it did not
        work", it was "I have not noticed yet". The app could not tell those
        apart and treated the second as the first.

        Polling rather than one long sleep so the usual case still returns as
        soon as the plug catches up, rather than always costing the full wait.

        An unreachable plug is a different failure and keeps its own message. It
        only counts as unreachable if it never answered once across the whole
        window: a single dropped packet from a plug at -87 dBm behind a bed is
        not a reason to give up on a command.
        """
        want = "off" if off else "on"
        waited = float(self.settings.power_settle_s)
        await self.clock.sleep(waited)

        heard = False
        while True:
            watts = await self.power.read_watts()
            if watts is not None:
                heard = True
                reads_off = watts < self.settings.off_threshold_w
                if reads_off is off:
                    if waited > self.settings.power_settle_s:
                        self.events.info(
                            "power",
                            f"The plug took {waited:.0f}s to report the unit {want}",
                        )
                    return True

            if waited >= self.settings.power_confirm_s:
                if not heard:
                    raise CommandFailed(
                        f"Plug unreachable, cannot confirm the unit powered {want}"
                    )
                return False

            step = min(
                float(self.settings.power_poll_s), self.settings.power_confirm_s - waited
            )
            await self.clock.sleep(step)
            waited += step
