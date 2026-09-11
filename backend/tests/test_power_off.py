"""The morning of 11 September, which the app spent pressing power at a bed that
had already switched off.

The log Liam sent, in order:

    07:30  Pressed power twice, twice over, and the plug still reads on
           The board is answering but nothing is reaching the unit, so it is
           being restarted and tried once more.
    07:31  Pressed power twice, twice over, and the plug still reads on.
           The unit will not switch itself off.
    07:31  The power_off step did not land. Retrying every 30s.
    07:31  ... and again, and again.

Eight presses of power, two board restarts, and a bed still running at breakfast.
Nothing was broken. Two separate mistakes conspired:

**One press too many.** The unit takes two steps to switch off, not two presses:
a press wakes the display, and the next press switches it off. The old gesture
sent a `temp_down` preamble, which lit the display, and then a *pair* of power
presses. The first switched the unit off. The second, six tenths of a second
later, switched it back on. Every single time.

**A plug that is slower than the check.** A Shelly does not report a change of
draw for the better part of a minute. The check waited ten seconds, read the
draw from before the presses, and concluded nothing had landed, so it sent the
gesture again and then restarted a board that was working perfectly.

None of this could show up in a test, because the fake plug answered instantly
and the fake unit had been taught the wrong model of the button. Both are fixed,
which is most of what this file is here to hold in place.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.adapters.fake_power import REPORTING_LAG_S, FakePowerMonitor
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Button, Power, Schedule
from hydrosnooze.sequences import CommandFailed, NotLanding
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 11, 7, 30)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tmp_path):
    clock = VirtualClock(NOW)
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=clock, echo=False)
    svc.schedule = Schedule(wake_time=time(7, 30), days_of_week=[0, 1, 2, 3, 4, 5, 6])
    yield svc
    svc.db.close()


def running(service):
    """A unit part way through a night, with the display long since dark."""
    service.unit.powered = True
    service.unit.powered_at = service.clock.now() - timedelta(hours=8)
    service.unit.display_awake_until = None
    service.unit.adjusting = False
    return service.unit


# --- The plug, which is the thing nobody modelled --------------------------------


async def test_the_plug_lies_for_the_best_part_of_a_minute(rig):
    """The measurement this all turns on. Liam watched a unit that had visibly
    switched off still reading as running more than thirty seconds later."""
    rig.unit_on(display_dark=False)
    assert await rig.power.read_watts() > 100

    rig.unit.powered = False
    assert await rig.power.read_watts() > 100, "the plug has not noticed yet"

    rig.clock.advance(timedelta(seconds=REPORTING_LAG_S + 1))
    assert await rig.power.read_watts() < 3


async def test_the_old_ten_second_check_sat_inside_that_lag(rig, settings):
    """Which is why it kept reporting a failure that had not happened."""
    assert settings.power_settle_s < REPORTING_LAG_S
    assert settings.power_confirm_s > REPORTING_LAG_S, "and the new one does not"


# --- The gesture ----------------------------------------------------------------


async def test_switching_off_sends_exactly_one_press_of_power(service):
    running(service)
    await service.power_off()

    assert not service.unit.powered
    assert service.transmitter.count(Button.POWER) == 1
    assert service.state.power is Power.OFF


async def test_it_waits_the_plug_out_instead_of_pressing_again(service):
    """The heart of it. The unit goes off immediately; the plug spends the next
    thirty five seconds insisting it is still running. The app has to sit through
    that without touching the button."""
    running(service)
    await service.power_off()

    assert service.transmitter.count(Button.POWER) == 1, "it pressed again mid-lag"
    assert service.clock.now() - NOW > timedelta(seconds=REPORTING_LAG_S), "it did not wait"


async def test_the_old_gesture_is_what_turned_the_bed_back_on(service):
    """Reproduced against the unit rather than the app, so it stays true even if
    this file is the only thing left describing it."""
    unit = running(service)
    await service.commands.wake()

    await service.transmitter.press(Button.POWER, "off")
    assert not unit.powered

    await service.transmitter.press(Button.POWER, "the spare press the app used to send")
    assert unit.powered, "and there is the bed still running at breakfast"


# --- Restarting the board, which should now be rare and is no longer per attempt ---


class Wedged:
    """A blaster that answers everything and emits nothing until it is rebooted."""

    def __init__(self, unit, *, cured_by_reboot: bool):
        self.unit = unit
        self.cured_by_reboot = cured_by_reboot
        self.reboots = 0

    async def reboot(self):
        self.reboots += 1
        if self.cured_by_reboot:
            self.unit.deaf = False


async def test_the_first_failure_does_not_reach_for_the_board(service):
    """One press that vanished is not a broken board. The cheap fix is another
    gesture five minutes later, and that is what the retry above is for."""
    unit = running(service)
    unit.deaf = True
    stub = Wedged(unit, cured_by_reboot=True)
    service.transmitter.reboot = stub.reboot

    assert await service._run_power_off(service.schedule.plan_for(NOW.date())) is False
    assert stub.reboots == 0, "it restarted the board on the first sign of trouble"


async def test_a_board_that_really_is_wedged_is_still_restarted(service):
    """The restart was never the wrong idea, only the wrong trigger. By the second
    failure the plug has been asked for four solid minutes across two separate
    gestures and has never once agreed, which is a pattern rather than a blip."""
    unit = running(service)
    unit.deaf = True
    stub = Wedged(unit, cured_by_reboot=True)
    service.transmitter.reboot = stub.reboot
    plan = service.schedule.plan_for(NOW.date())

    assert await service._run_power_off(plan) is False
    assert await service._run_power_off(plan) is True
    assert stub.reboots == 1
    assert not unit.powered


async def test_a_dead_board_is_restarted_once_a_night_not_once_an_attempt(service):
    """The window is two hours. A board that is genuinely dead must not be power
    cycled for the whole of it, which is what one restart per attempt meant."""
    unit = running(service)
    unit.deaf = True
    stub = Wedged(unit, cured_by_reboot=False)
    service.transmitter.reboot = stub.reboot
    plan = service.schedule.plan_for(NOW.date())

    for _ in range(4):
        assert await service._run_power_off(plan) is False

    assert stub.reboots == 1, "it restarted the board on every attempt"


async def test_a_second_night_gets_its_own_restart(service):
    """Keyed on the night rather than latched forever, because tomorrow's board
    deserves the same chance as tonight's."""
    unit = running(service)
    unit.deaf = True
    stub = Wedged(unit, cured_by_reboot=False)
    service.transmitter.reboot = stub.reboot

    tonight = service.schedule.plan_for(NOW.date())
    tomorrow = service.schedule.plan_for((NOW + timedelta(days=1)).date())
    for plan in (tonight, tonight, tomorrow, tomorrow):
        await service._run_power_off(plan)

    assert stub.reboots == 2


async def test_it_shouts_once_a_night_rather_than_every_seven_minutes(service):
    """The window is two hours and the retry runs all of it. A board that is
    genuinely dead used to put twenty identical alarms on the phone over
    breakfast, and the second one tells you nothing the first did not."""
    unit = running(service)
    unit.deaf = True
    stub = Wedged(unit, cured_by_reboot=False)
    service.transmitter.reboot = stub.reboot
    plan = service.schedule.plan_for(NOW.date())

    for _ in range(5):
        await service._run_power_off(plan)

    shouted = [e for e in service.events.recent(60) if e.level == "error" and e.kind == "power_off"]
    assert len(shouted) == 1, "it alarmed on every attempt"
    assert "will not switch itself off" in shouted[0].message

    quiet = [
        e for e in service.events.recent(60) if e.level == "info" and "Still trying" in e.message
    ]
    assert quiet, "and it went quiet rather than silent"


async def test_tomorrow_night_is_shouted_about_again(service):
    """Keyed on the night, so a fault that comes back is reported afresh."""
    unit = running(service)
    unit.deaf = True
    service.transmitter.reboot = Wedged(unit, cured_by_reboot=False).reboot

    await service._run_power_off(service.schedule.plan_for(NOW.date()))
    await service._run_power_off(
        service.schedule.plan_for((NOW + timedelta(days=1)).date())
    )

    shouted = [e for e in service.events.recent(60) if e.level == "error" and e.kind == "power_off"]
    assert len(shouted) == 2


# --- Switching on has the same disease and the same cure -------------------------


async def test_powering_on_does_not_press_again_inside_the_lag(service):
    """A second press of power at a unit that is already on, with a display lit
    by the first press, switches it straight back off. Same shape of bug, at the
    other end of the night."""
    service.unit.powered = False
    await service.power_on()

    assert service.unit.powered
    assert service.transmitter.count(Button.POWER) == 1


# --- And the failure that is genuinely a failure ---------------------------------


async def test_an_unreachable_plug_is_reported_as_that_and_not_as_a_wedged_board(service):
    """Different fault, different message, and no board gets restarted for it."""
    running(service)
    service.power.offline = True
    stub = Wedged(service.unit, cured_by_reboot=False)
    service.transmitter.reboot = stub.reboot

    with pytest.raises(CommandFailed, match="unreachable"):
        await service.commands.power_off()
    assert stub.reboots == 0


async def test_one_dropped_packet_does_not_abandon_the_command(service):
    """A plug at -87 dBm behind a bed drops about one read in twenty. That is not
    a reason to give up on switching the bed off."""
    unit = running(service)
    real = service.power.read_watts
    reads = {"n": 0}

    async def flaky():
        reads["n"] += 1
        return None if reads["n"] in (2, 3) else await real()

    service.power.read_watts = flaky
    await service.commands.power_off()
    assert not unit.powered


async def test_nothing_landing_at_all_is_a_not_landing(service):
    """Which is the one failure this project can prove, and the only one that
    earns an automatic restart."""
    unit = running(service)
    unit.deaf = True
    with pytest.raises(NotLanding):
        await service.commands.power_off()


async def test_the_fake_plug_is_only_slow_when_it_has_a_clock(rig):
    """So a test that wants the old instant plug can still have one."""
    instant = FakePowerMonitor(rig.unit)
    assert instant.lag_s == 0.0
