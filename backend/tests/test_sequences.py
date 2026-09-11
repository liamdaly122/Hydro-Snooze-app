"""The rail counts and the wake preamble, proved before any infrared is emitted.

Getting a press count wrong here is the easiest mistake in the project to make and
the hardest to notice, because the unit cannot be read back. So every sequence is
run against the simulated unit from every plausible starting state and the unit's
exact final state is asserted.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hydrosnooze.models import Button, Mode, rail_count
from hydrosnooze.sequences import CommandFailed, NotLanding

pytestmark = pytest.mark.asyncio


# --- Temperature --------------------------------------------------------------


@pytest.mark.parametrize("start_temp", [15, 20, 27, 35])
@pytest.mark.parametrize("display_dark", [True, False])
@pytest.mark.parametrize("target", [15, 19, 25, 30])
async def test_set_temperature_lands_exactly_from_any_starting_state(
    rig, start_temp, display_dark, target
):
    # Whatever the unit was showing, and whether or not the display had gone dark,
    # railing to the minimum and counting up gets to the same place.
    rig.unit_on(mode=Mode.QUIET, target=start_temp, display_dark=display_dark)
    await rig.commands.set_temperature(target, Mode.QUIET)
    assert rig.unit.target == target


async def test_set_temperature_sends_exactly_two_plus_rail_plus_count(rig):
    rig.unit_on(mode=Mode.QUIET, target=35)
    await rig.commands.set_temperature(19, Mode.QUIET)
    # 2 preamble + 25 rail + 4 up
    assert rig.tx.presses_sent == 2 + 25 + 4


async def test_warming_rails_thirty_five_not_twenty_five(rig):
    # Warming runs 25 to 55, so the rail is ten presses longer. Using the cooling
    # count here would leave the unit ten degrees off.
    rig.unit_on(mode=Mode.WARMING, target=55)
    await rig.commands.set_temperature(28, Mode.WARMING)
    assert rig.unit.target == 28
    assert rig.tx.presses_sent == 2 + rail_count(Mode.WARMING) + 3


async def test_the_rail_absorbs_the_preamble(rig):
    # The two discarded presses lower the target when the display was already
    # awake. The rail has five presses of margin precisely so that does not matter.
    rig.unit_on(mode=Mode.QUIET, target=20, display_dark=False)
    rig.unit.adjusting = True  # presses will act immediately
    await rig.commands.set_temperature(22, Mode.QUIET)
    assert rig.unit.target == 22


async def test_the_default_cap_is_the_units_own_maximum(settings):
    # Liam's call: no software ceiling beyond what the hardware allows. Which
    # makes the Shelly's own auto-off timer the only thing limiting how long a hot
    # bed stays hot, and why SETUP.md treats setting it as required.
    assert settings.max_temperature_c == 55


async def test_a_lowered_cap_is_still_enforced(clock):
    # The setting still does its job for anyone who wants a ceiling back.
    from hydrosnooze.adapters import build_adapters
    from hydrosnooze.config import Settings
    from hydrosnooze.events import EventLog
    from hydrosnooze.sequences import Commands

    capped = Settings(max_temperature_c=30)
    tx, power, unit = build_adapters(capped, clock, echo=False)
    commands = Commands(tx, power, clock, capped, EventLog(clock))
    unit.powered, unit.powered_at, unit.mode = True, clock.now(), Mode.WARMING

    with pytest.raises(CommandFailed, match="safety cap"):
        await commands.set_temperature(31, Mode.WARMING)


async def test_temperature_outside_the_modes_range_is_refused(rig):
    rig.unit_on(mode=Mode.QUIET)
    with pytest.raises(CommandFailed, match="outside"):
        await rig.commands.set_temperature(14, Mode.QUIET)


# --- Mode ---------------------------------------------------------------------


@pytest.mark.parametrize("start", list(Mode))
@pytest.mark.parametrize("target", list(Mode))
async def test_set_mode_reaches_every_mode_from_every_mode(rig, start, target):
    # Warming is an absolute destination, so warm-then-cool gives a deterministic
    # route to any mode without ever needing to know where the unit started.
    rig.unit_on(mode=start)
    await rig.commands.set_mode(target)
    assert rig.unit.mode is target


async def test_set_mode_uses_the_documented_press_count(rig):
    rig.unit_on(mode=Mode.QUIET)
    await rig.commands.set_mode(Mode.TURBO)
    # 2 preamble + 1 warm + 3 cool
    assert rig.tx.presses_sent == 6


# --- The wake preamble --------------------------------------------------------


async def test_a_command_lands_even_when_the_display_has_gone_dark(rig):
    """The preamble still earns its place.

    Stage boundaries fall hours apart, so the display is always dark by the time
    the next one arrives, and the first press of anything is eaten waking it.
    """
    rig.unit_on(mode=Mode.QUIET, target=30, display_dark=True)
    await rig.commands.set_temperature(18, Mode.QUIET)
    assert rig.unit.target == 18


async def test_without_a_preamble_the_first_press_is_lost(rig):
    # What the two discarded presses are for, shown directly.
    rig.unit_on(mode=Mode.QUIET, target=20, display_dark=True)
    result = rig.unit.press(Button.TEMP_DOWN)
    assert result.swallowed
    assert rig.unit.target == 20


async def test_the_unit_switches_itself_off_after_twelve_hours_untouched(rig):
    """The cutoff that cannot be disabled.

    Every stage transition resets it, so across a normal night it never fires.
    It is a backstop of last resort, not something the app relies on.
    """
    import datetime as _dt

    rig.unit_on(display_dark=True)
    rig.clock.advance(_dt.timedelta(hours=11, minutes=59))
    assert rig.unit.describe() != "OFF"
    rig.clock.advance(_dt.timedelta(minutes=2))
    assert rig.unit.describe() == "OFF"


async def test_muting_stops_the_unit_beeping_all_night(rig):
    # Roughly thirty presses land at each stage boundary, at two in the morning,
    # next to a bed.
    rig.unit_on(display_dark=True)
    await rig.commands.mute()
    assert any("mute" in line for line in rig.tx.lines)


# --- Power --------------------------------------------------------------------


async def test_power_on_verifies_against_the_plug(rig):
    assert not rig.unit.powered
    await rig.commands.power_on()
    assert rig.unit.powered
    assert await rig.power.read_watts() > 100


async def test_power_on_is_a_no_op_when_already_on(rig):
    rig.unit_on()
    await rig.commands.power_on()
    assert rig.tx.presses_sent == 0


# --- Switching off, which sent eight presses and left the bed running -----------
#
# Two steps, not two presses. Liam works the remote as "one to turn the display
# on and then one to turn off the unit", and only the second step has to be
# power: this file has always had a safer way to wake a display. So the gesture
# is the `temp_down` preamble and then a single press of power, from every
# starting state, and a second press of power is not a correction. It is the
# thing that switches a unit that has just gone off back on again.


@pytest.mark.parametrize("display_dark", [True, False])
async def test_power_off_lands_from_either_display_state(rig, display_dark):
    rig.unit_on(display_dark=display_dark)
    await rig.commands.power_off()
    assert not rig.unit.powered


@pytest.mark.parametrize("display_dark", [True, False])
async def test_power_is_pressed_exactly_once(rig, display_dark):
    """The whole fix, in one assertion.

    It used to be two, and the second one is what left the bed running every
    morning. Deterministic across both display states, which is why the preamble
    is worth its two harmless presses: nothing here has to guess.
    """
    rig.unit_on(display_dark=display_dark)
    await rig.commands.power_off()
    assert rig.tx.count(Button.POWER) == 1
    assert rig.tx.count(Button.TEMP_DOWN) == 2, "the wake preamble, which cannot switch anything on"


async def test_a_second_press_turns_it_straight_back_on(rig):
    """The bug of 11 September, reproduced against the unit rather than the app.

    This is what the old gesture did on every night of the week. The preamble lit
    the display, the first press of power switched the unit off, and the second,
    six tenths of a second later, switched it back on. Then the plug was asked ten
    seconds later, said "still running", and the whole thing went again.
    """
    rig.unit_on(display_dark=True)
    await rig.commands.wake()
    await rig.tx.press(Button.POWER, "off")
    assert not rig.unit.powered, "one press, on a lit display, is all it takes"

    await rig.tx.press(Button.POWER, "the spare one")
    assert rig.unit.powered, "and the spare press is what was undoing it"


async def test_a_dark_display_still_eats_the_first_press_of_anything(rig):
    """Which is why the preamble is there at all, and why it is not power."""
    rig.unit_on(display_dark=True)
    await rig.tx.press(Button.POWER, "into a dark display")
    assert rig.unit.powered, "swallowed waking the display"


async def test_power_off_is_still_a_no_op_when_the_plug_says_it_is_off(rig):
    await rig.commands.power_off()
    assert rig.tx.presses_sent == 0


async def test_an_unreachable_plug_fails_rather_than_guessing(rig):
    rig.power.offline = True
    with pytest.raises(CommandFailed, match="unreachable"):
        await rig.commands.power_on()


# --- Cooling and warming in the same night ------------------------------------


async def test_the_unit_moves_between_cooling_and_warming_freely(rig):
    """The whole reason for dropping the unit's own scheduler.

    While its Smart Sleep Schedule runs, the unit refuses to switch between
    cooling and warming, which capped every night at "somewhere at or below the
    bedroom". Outside it, this is just two commands.
    """
    rig.unit_on(mode=Mode.QUIET, display_dark=True)
    await rig.commands.set_mode(Mode.QUIET)
    await rig.commands.set_temperature(17, Mode.QUIET)
    assert rig.unit.target == 17

    await rig.commands.set_mode(Mode.WARMING)
    await rig.commands.set_temperature(28, Mode.WARMING)
    assert rig.unit.mode is Mode.WARMING
    assert rig.unit.target == 28


async def test_a_whole_night_of_transitions_lands_on_every_temperature(rig):
    rig.unit_on(mode=Mode.TURBO, target=33, display_dark=True)
    for mode, temp in [(Mode.QUIET, 17), (Mode.QUIET, 20), (Mode.WARMING, 26)]:
        await rig.commands.set_mode(mode)
        await rig.commands.set_temperature(temp, mode)
        assert rig.unit.mode is mode
        assert rig.unit.target == temp


# --- The mute button ----------------------------------------------------------


async def test_mute_is_a_toggle_the_unit_remembers(rig):
    """Why muting is never automatic.

    The unit saves this setting. Firing it on every power on would unmute it
    every other night, and the press that unmuted it would beep.
    """
    rig.unit_on(display_dark=True)
    assert not rig.unit.muted

    await rig.commands.mute()
    assert rig.unit.muted

    await rig.commands.mute()
    assert not rig.unit.muted, "a second mute press unmutes, which is the trap"


async def test_muting_survives_a_power_cycle(rig):
    rig.unit_on(display_dark=True)
    await rig.commands.mute()
    assert rig.unit.muted

    await rig.commands.power_off()
    await rig.commands.power_on()
    assert rig.unit.muted, "the unit remembers it, so there is nothing to re-send"


async def test_a_whole_night_never_touches_the_mute_button(rig):
    # The bug this replaced: mute fired at every power on, so the unit spent
    # every other night beeping through thirty presses at two in the morning.
    rig.unit_on(display_dark=True)
    await rig.commands.mute()
    rig.tx.lines.clear()

    for mode, temp in [(Mode.QUIET, 17), (Mode.QUIET, 20), (Mode.WARMING, 26)]:
        await rig.commands.set_mode(mode)
        await rig.commands.set_temperature(temp, mode)

    assert not any("mute" in line for line in rig.tx.lines)
    assert rig.unit.muted


# --- The button in the app, which is not a sequence -----------------------------
#
# Everything else in sequences.py is built to be safe when nobody is watching:
# read the plug, work out what is needed, send it, confirm it landed. That is
# right at three in the morning and wrong for someone standing in front of the
# bed, who wanted the button to do what the button on the remote does.


async def test_the_app_button_sends_exactly_one_press(rig):
    rig.unit_on(display_dark=True)
    await rig.commands.press_power()
    assert rig.tx.count(Button.POWER) == 1
    assert rig.tx.presses_sent == 1, "no wake preamble either"


async def test_it_does_not_ask_the_plug_first(rig):
    """power_on and power_off both return early when the plug says there is
    nothing to do. This one has no opinion about that: it was asked to send a
    press, so it sends a press."""
    rig.power.offline = True
    await rig.commands.press_power()
    assert rig.tx.count(Button.POWER) == 1


async def test_it_does_not_check_afterwards_or_try_again(rig):
    """A single press on a dark display does nothing at all, and that is the
    correct outcome here rather than a problem to solve. Tapping twice is how you
    switch the unit off, because that is what the unit wants."""
    rig.unit_on(display_dark=True)
    await rig.commands.press_power()
    assert rig.unit.powered, "swallowed by the dark display, and left alone"
    assert rig.tx.presses_sent == 1


async def test_two_taps_switch_a_dark_unit_off(rig):
    """Which is the whole reason a single press is a reasonable button.

    Two taps from dark is exactly what the hand does on the remote: the first
    wakes the display and the second switches the unit off.
    """
    rig.unit_on(display_dark=True)
    await rig.commands.press_power()
    await rig.commands.press_power()
    assert not rig.unit.powered


async def test_one_tap_is_enough_once_the_display_is_lit(rig):
    """And the corollary, which is the thing the scheduled gesture relies on."""
    rig.unit_on(display_dark=False)
    await rig.commands.press_power()
    assert not rig.unit.powered


async def test_the_scheduled_power_off_keeps_its_preamble_and_its_plug_check(rig):
    """Nobody is watching at the wake time, so that one still wakes the display
    deliberately and still confirms against the plug afterwards. What it no
    longer does is send a press it does not need."""
    rig.unit_on(display_dark=True)
    await rig.commands.power_off()
    assert not rig.unit.powered
    assert rig.tx.count(Button.POWER) == 1
    assert rig.tx.count(Button.TEMP_DOWN) == 2, "the wake preamble"


# --- Curing the one failure that can be proved ----------------------------------
#
# A temperature press has no readback: one that vanished into a wedged board looks
# exactly like one that worked, so there is nothing to react to. Power is the
# exception, because the plug is watching. A unit that was off and stays off
# through two presses of power did not receive them, and that is a fact rather
# than a guess.


async def test_a_press_that_provably_did_not_arrive_has_its_own_type(rig):
    """So the one failure with a remedy can be told from the several without."""
    rig.unit.deaf = True  # the board answers, the unit hears nothing
    with pytest.raises(NotLanding):
        await rig.commands.power_on()


async def test_an_unreachable_plug_is_not_that_failure(rig):
    """Not knowing is not the same as knowing it failed, and rebooting a board
    because a plug went quiet would be answering the wrong question."""
    rig.power.offline = True
    with pytest.raises(CommandFailed) as caught:
        await rig.commands.power_on()
    assert not isinstance(caught.value, NotLanding)
