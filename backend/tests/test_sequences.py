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


async def test_arm_schedule_works_when_the_display_has_gone_dark(rig):
    rig.unit_on(mode=Mode.QUIET, display_dark=True)
    await rig.commands.arm_schedule()
    assert rig.unit.schedule_running()


async def test_arming_without_the_preamble_silently_fails_to_arm(rig):
    """The 3am failure, caught here instead.

    A single schedule press onto a dark display only wakes it. Without the
    preamble the app would report success, nothing would be armed, and the first
    anyone would know is waking up hot at 3am.
    """
    rig.unit_on(mode=Mode.QUIET, display_dark=True)
    await rig.tx.press(Button.SCHEDULE, "no preamble")
    await rig.clock.sleep(20)
    assert not rig.unit.schedule_running()
    assert rig.unit.wizard_phase is None


async def test_arming_is_self_correcting_if_a_press_is_dropped(rig):
    # Landing in phase 2 rather than phase 1 makes no difference: doing nothing
    # for eight seconds applies the saved temperatures either way.
    rig.unit_on(mode=Mode.QUIET, display_dark=False)
    rig.unit.press(Button.SCHEDULE)  # phase 1
    rig.unit.press(Button.SCHEDULE)  # phase 2
    await rig.clock.sleep(20)
    assert rig.unit.schedule_running()


async def test_the_preamble_is_swallowed_without_waking_during_a_schedule(rig):
    """Why the preamble must never be sent during an active schedule.

    Temperature presses are ignored there and do not wake the display either, so
    the app would believe the unit was awake when it was not, and the next press
    would be eaten.
    """
    rig.unit_on(mode=Mode.QUIET, display_dark=True)
    await rig.commands.arm_schedule()
    assert rig.unit.schedule_running()

    rig.clock.advance(__import__("datetime").timedelta(minutes=6))  # display dark again
    await rig.commands.wake()
    assert rig.unit.display_dark(rig.clock.now()), "the preamble did not wake it, as expected"


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


# --- Writing the schedule -----------------------------------------------------


async def test_write_schedule_leaves_the_right_temperatures_on_the_unit(rig):
    rig.unit_on(mode=Mode.TURBO, display_dark=True)
    await rig.commands.write_schedule((19, 17, 21), Mode.QUIET)
    assert rig.unit.phase_temps == [19, 17, 21]
    assert rig.unit.mode is Mode.QUIET
    assert rig.unit.schedule_running()


async def test_write_schedule_reports_progress_that_adds_up(rig):
    frames = []
    rig.unit_on(display_dark=True)
    await rig.commands.write_schedule((19, 17, 21), Mode.QUIET, on_progress=frames.append)
    assert frames[-1].phase == "done"
    assert frames[-1].presses_sent == frames[-1].presses_total
    assert frames[-1].presses_total == rig.tx.presses_sent


async def test_write_schedule_refuses_a_phase_above_the_safety_cap(rig, settings):
    rig.unit_on()
    with pytest.raises(CommandFailed, match="safety cap"):
        await rig.commands.write_schedule((19, settings.max_temperature_c + 1, 21), Mode.QUIET)


# --- Dropping out of turbo mid-schedule ---------------------------------------


async def test_cooling_speed_can_be_changed_during_a_schedule(rig):
    rig.unit_on(mode=Mode.TURBO, display_dark=True)
    await rig.commands.arm_schedule()
    # Sent immediately, while the display is still awake from arming.
    await rig.commands.set_cooling_speed(Mode.QUIET, Mode.TURBO)
    assert rig.unit.mode is Mode.QUIET
    assert rig.unit.schedule_running()


async def test_warming_cannot_be_reached_mid_schedule(rig):
    rig.unit_on(mode=Mode.TURBO)
    with pytest.raises(CommandFailed, match="cannot be switched"):
        await rig.commands.set_cooling_speed(Mode.WARMING, Mode.TURBO)
