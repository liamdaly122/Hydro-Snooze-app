"""The rail counts and the wake preamble, proved before any infrared is emitted.

Getting a press count wrong here is the easiest mistake in the project to make and
the hardest to notice, because the unit cannot be read back. So every sequence is
run against the simulated unit from every plausible starting state and the unit's
exact final state is asserted.
"""

from __future__ import annotations

import pytest

from hydrosnooze.models import Button, Mode, rail_count
from hydrosnooze.sequences import CommandFailed

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


async def test_temperature_above_the_safety_cap_is_refused(rig, settings):
    rig.unit_on(mode=Mode.WARMING, target=30)
    with pytest.raises(CommandFailed, match="safety cap"):
        await rig.commands.set_temperature(settings.max_temperature_c + 1, Mode.WARMING)


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


async def test_power_off_sends_two_presses_for_a_dark_display(rig):
    rig.unit_on(display_dark=True)
    await rig.commands.power_off()
    assert not rig.unit.powered
    assert rig.tx.presses_sent == 2


async def test_power_off_corrects_itself_when_the_two_presses_cancel_out(rig):
    # Display already awake, so press one turns it off and press two turns it
    # straight back on. The third press is the correction.
    rig.unit_on(display_dark=False)
    await rig.commands.power_off()
    assert not rig.unit.powered
    assert rig.tx.presses_sent == 3


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
