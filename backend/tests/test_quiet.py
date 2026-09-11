"""Preferring the quiet half of the unit, decided from the bed rather than the plan.

Warming mode on this unit sounds like a geiger counter and it runs next to
someone trying to sleep. Cooling is silent. Both can be set to any number between
25 and 35, so for most of a night there is a genuine choice, and until the probes
went on there was no way to make it: `mode_for_target` decides at plan time, from
the stage before it, about a bed with nobody in it.

That prediction is wrong in exactly the case that matters. A stage that steps the
temperature up is planned as warming, but with a body in the bed it is already at
the number, and warming has nothing to do except make a noise.

The asymmetry underneath all of this: cooling holds a bed at a number by taking
away what the body puts in, and it cannot put heat back. So the moment the bed
genuinely loses ground, only warming will do and the noise is worth it again.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.adapters.probes import RETURN, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import (
    QUIET_ARRIVED_C,
    QUIET_FALLEN_C,
    Mode,
    Power,
    Schedule,
    SleepStage,
    Stage,
    quieter_mode,
)
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import MODE_DWELL, Service

NOW = datetime(2026, 9, 11, 2, 0)


def decide(target, running, bed, speed=Mode.QUIET, cap=30):
    return quieter_mode(target, running, bed, speed, cap_c=cap)


# --- The decision on its own ----------------------------------------------------


def test_a_bed_that_has_arrived_hands_over_to_the_quiet_mode():
    assert decide(27, Mode.WARMING, 27.0) is Mode.QUIET
    assert decide(27, Mode.WARMING, 26.5) is Mode.QUIET


def test_a_bed_still_climbing_is_left_to_warm():
    """Cooling cannot get a bed to a number above where it is, so handing over
    early would leave it short for the rest of the stage."""
    assert decide(27, Mode.WARMING, 25.0) is None
    assert decide(27, Mode.WARMING, 26.4) is None


def test_a_bed_losing_ground_gets_the_noise_back():
    assert decide(27, Mode.QUIET, 25.0) is Mode.WARMING
    assert decide(27, Mode.QUIET, 24.0) is Mode.WARMING


def test_the_band_between_the_two_changes_nothing():
    """The whole design is the gap between the thresholds.

    A single line would have the unit swapping modes on every half degree of
    probe wobble, thirty-five presses at a time, all night.
    """
    for bed in (26.4, 26.0, 25.6, 25.2):
        assert decide(27, Mode.QUIET, bed) is None, bed
    assert QUIET_FALLEN_C - QUIET_ARRIVED_C >= 1.0


def test_the_speed_it_hands_over_to_is_the_one_on_the_schedule():
    assert decide(27, Mode.WARMING, 27.0, speed=Mode.TURBO) is Mode.TURBO
    assert decide(27, Mode.WARMING, 27.0, speed=Mode.STANDARD) is Mode.STANDARD


@pytest.mark.parametrize("target", [17, 20, 24])
def test_below_the_overlap_there_is_nothing_to_decide(target):
    """Warming mode cannot express a number below 25, so cooling was never a
    preference there, it was the only option."""
    assert decide(target, Mode.QUIET, target - 5) is None


@pytest.mark.parametrize("target", [36, 40])
def test_above_the_overlap_there_is_nothing_to_decide_either(target):
    assert decide(target, Mode.WARMING, target) is None


def test_it_never_asks_for_a_number_past_the_safety_cap():
    """The cap is the one rule in this project that outranks comfort."""
    assert decide(32, Mode.QUIET, 28.0, cap=30) is None
    assert decide(29, Mode.QUIET, 26.0, cap=30) is Mode.WARMING


def test_no_reading_means_no_opinion():
    """The oldest rule here. Never act on a value that has not been confirmed."""
    assert decide(27, Mode.WARMING, None) is None
    assert decide(27, Mode.QUIET, None) is None


# --- The service acting on it ---------------------------------------------------


@pytest.fixture
def service(tmp_path):
    clock = VirtualClock(NOW)
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=clock,
        echo=False,
    )
    svc.schedule = Schedule(
        wake_time=NOW.time(),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[SleepStage(Stage.DEEP, 240, 24), SleepStage(Stage.REM, 240, 27)],
    )
    yield svc
    svc.db.close()


def bed(service, celsius: float) -> None:
    service.probes.readings[RETURN] = Reading(celsius, service.clock.now())


def step_at(service, temp_c: int, mode: Mode):
    """Put the service in the middle of a stage, in a known mode."""
    from hydrosnooze.models import StageStep

    service._set_state(power=Power.ON, assumed_mode=mode, assumed_target_c=temp_c)
    return StageStep(
        stage=Stage.REM,
        starts_at=service.clock.now() - timedelta(hours=1),
        ends_at=service.clock.now() + timedelta(hours=1),
        temp_c=temp_c,
        mode=mode,
    )


@pytest.mark.asyncio
async def test_a_stage_that_opens_warming_on_a_warm_bed_goes_quiet_at_once(service):
    """No dwell on the first correction, deliberately.

    Half an hour of the noise this feature exists to remove would be a strange
    way to start.
    """
    step = step_at(service, 27, Mode.WARMING)
    bed(service, 27.1)

    await service._correct_mode(step, Power.ON, service.clock.now())

    assert service.state.assumed_mode is Mode.QUIET
    assert service.state.assumed_target_c == 27
    said = [e for e in service.events.recent(20) if "quiet half" in e.message]
    assert said and "27.1C" in said[0].message


@pytest.mark.asyncio
async def test_it_will_not_swap_again_for_half_an_hour(service):
    step = step_at(service, 27, Mode.WARMING)
    bed(service, 27.1)
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert service.state.assumed_mode is Mode.QUIET

    service.clock.advance(MODE_DWELL - timedelta(minutes=1))
    bed(service, 24.0)
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert service.state.assumed_mode is Mode.QUIET, "too soon"

    service.clock.advance(timedelta(minutes=2))
    bed(service, 24.0)
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert service.state.assumed_mode is Mode.WARMING


@pytest.mark.asyncio
async def test_a_bed_that_loses_ground_gets_warmed_again_and_says_why(service):
    step = step_at(service, 27, Mode.QUIET)
    bed(service, 24.6)

    await service._correct_mode(step, Power.ON, service.clock.now())

    assert service.state.assumed_mode is Mode.WARMING
    said = [e for e in service.events.recent(20) if "warming again" in e.message]
    assert said and "24.6C" in said[0].message


@pytest.mark.asyncio
async def test_nothing_happens_between_stages(service):
    """Before bedtime the bed is empty and getting it ready is a different job
    with its own rules."""
    bed(service, 27.1)
    service._set_state(power=Power.ON, assumed_mode=Mode.WARMING)
    await service._correct_mode(None, Power.ON, service.clock.now())
    assert service.state.assumed_mode is Mode.WARMING


@pytest.mark.asyncio
async def test_nothing_happens_with_the_unit_off(service):
    step = step_at(service, 27, Mode.WARMING)
    bed(service, 27.1)
    await service._correct_mode(step, Power.OFF, service.clock.now())
    assert service.state.assumed_mode is Mode.WARMING


@pytest.mark.asyncio
async def test_a_quiet_probe_board_leaves_the_night_exactly_as_it_was(service):
    """The rule that has held since the probes went on: they are the one thing
    here a night does not depend on."""
    step = step_at(service, 27, Mode.WARMING)
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert service.state.assumed_mode is Mode.WARMING
    assert not [e for e in service.events.recent(20) if "quiet half" in e.message]


@pytest.mark.asyncio
async def test_a_whole_night_settles_rather_than_flapping(service):
    """The failure worth guarding against is not a wrong mode, it is thirty-five
    presses every few minutes until morning."""
    step = step_at(service, 27, Mode.WARMING)
    swaps = 0
    was = service.state.assumed_mode

    # Eight hours at the real sampling interval, on a bed hovering right around
    # the setpoint, which is the worst case for a threshold.
    for tick in range(8 * 60 * 2):
        service.clock.advance(timedelta(seconds=30))
        bed(service, 26.8 + (0.4 if tick % 2 else -0.4))
        await service._correct_mode(step, Power.ON, service.clock.now())
        if service.state.assumed_mode is not was:
            swaps += 1
            was = service.state.assumed_mode

    assert swaps == 1, f"settled once and stayed, got {swaps} swaps"


# --- Never taking the unit away from its owner ---------------------------------
#
# Found the evening this shipped, from a report that the app had gone dead: the
# power button did nothing and neither did the temperature. A correction holds the
# command lock through about forty presses, and a blaster that has gone away turns
# each of those into a connect and a timeout. Retried every thirty seconds, that
# is a comfort feature quietly monopolising the unit.


@pytest.mark.asyncio
async def test_a_correction_never_queues_behind_a_command(service):
    """Waiting for the lock would put a button someone just pressed behind forty
    presses of infrared, and the app would look dead while it happened."""
    step = step_at(service, 27, Mode.WARMING)
    bed(service, 27.1)

    await service._lock.acquire()
    try:
        await service._correct_mode(step, Power.ON, service.clock.now())
    finally:
        service._lock.release()

    assert service.state.assumed_mode is Mode.WARMING, "it waited its turn"


@pytest.mark.asyncio
async def test_a_correction_that_fails_backs_off_like_one_that_worked(service):
    """The bug. Starting the clock only on success meant a blaster that had gone
    away was retried on every single sample, holding the lock each time."""
    step = step_at(service, 27, Mode.WARMING)
    bed(service, 27.1)

    tried = 0

    async def refuse(mode, target):
        nonlocal tried
        tried += 1
        raise CommandFailed("the blaster is not answering")

    service._apply = refuse
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert tried == 1

    # The next few samples must not try again.
    for _ in range(10):
        service.clock.advance(timedelta(seconds=30))
        bed(service, 27.1)
        await service._correct_mode(step, Power.ON, service.clock.now())
    assert tried == 1, "retried a broken blaster on every sample"

    service.clock.advance(MODE_DWELL)
    bed(service, 27.1)
    await service._correct_mode(step, Power.ON, service.clock.now())
    assert tried == 2, "and does try again once the dwell has passed"


# --- Curing a board that answers but transmits nothing ---------------------------
#
# Liam's complaint, 10 September: the blaster showed green, the app reported no
# problem, and nothing reached the unit. Unplugging it and plugging it back in was
# the only cure, and it had to be done by hand, behind a bed, at night.


class Wedged:
    """A blaster that answers everything and emits nothing until it is rebooted."""

    def __init__(self, unit):
        self.unit = unit
        self.reboots = 0

    async def reboot(self):
        self.reboots += 1
        self.unit.deaf = False


@pytest.fixture
def wedged(service):
    service.unit.deaf = True
    stub = Wedged(service.unit)
    service.transmitter.reboot = stub.reboot
    return stub


@pytest.mark.asyncio
async def test_a_power_command_that_proves_nothing_arrives_reboots_the_board(service, wedged):
    """The whole point. Nobody is awake at seven in the morning to notice that
    the bed did not switch off, so the app has to notice instead."""
    await service.power_on()

    assert wedged.reboots == 1
    assert service.unit.powered, "and the retry after the reboot worked"
    assert service.state.power is Power.ON


@pytest.mark.asyncio
async def test_it_says_what_it_did_rather_than_healing_in_silence(service, wedged):
    """A board that needs rebooting nightly is a board on its way out, and that
    is invisible if the recovery never leaves a mark."""
    await service.power_on()
    said = [e for e in service.events.recent(20) if "restarted" in e.message]
    assert said and "nothing is reaching the unit" in said[0].message.lower()


@pytest.mark.asyncio
async def test_it_reboots_once_and_not_in_a_loop(service):
    """A genuinely broken board must not be power cycled every thirty seconds."""
    service.unit.deaf = True
    reboots = 0

    async def never_helps():
        nonlocal reboots
        reboots += 1

    service.transmitter.reboot = never_helps
    await service.power_on()

    assert reboots == 1
    assert service.state.power is Power.UNKNOWN, "and it ends up honest about it"


@pytest.mark.asyncio
async def test_an_unreachable_plug_does_not_reboot_anything(service):
    """Not knowing is not the same as knowing it failed. Rebooting a board
    because a plug went quiet answers a question nobody asked."""
    service.power.offline = True
    reboots = 0

    async def count():
        nonlocal reboots
        reboots += 1

    service.transmitter.reboot = count
    await service.power_on()
    assert reboots == 0


@pytest.mark.asyncio
async def test_the_nightly_switch_off_gets_the_same_treatment(service, wedged):
    """The one that matters most: nothing else turns the bed off, and nobody is
    awake to see that it did not."""
    service.unit.powered = True
    service.unit.deaf = True
    plan = service.schedule.plan_for(service.clock.now().date())

    assert await service._run_power_off(plan) is True
    assert wedged.reboots == 1
    assert not service.unit.powered


# --- A fresh board for every night ----------------------------------------------
#
# Both times the blaster wedged it had been powered up for days, answering the
# network the whole time and emitting nothing. This does not detect that state.
# It stops the board being in it when the night that matters arrives.


@pytest.mark.asyncio
async def test_the_blaster_is_restarted_before_the_bed_gets_ready(service):
    from hydrosnooze.scheduler import WAKE_BLASTER_BEFORE

    service.schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[SleepStage(Stage.DEEP, 240, 26), SleepStage(Stage.REM, 240, 27)],
    )
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    starts = plan.precool_at or plan.bedtime_at

    service.clock.jump_to(starts - WAKE_BLASTER_BEFORE - timedelta(minutes=1))
    assert service.scheduler.due(service.schedule, service.clock.now()) is None, "too early"

    service.clock.jump_to(starts - WAKE_BLASTER_BEFORE + timedelta(minutes=1))
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "wake_blaster"


@pytest.mark.asyncio
async def test_it_runs_once_a_night_like_every_other_job(service):
    """Through due(), not through the marks. Asserting that mark() then
    has_fired() agree is a round trip on a dict and passes for any job kind,
    including one due() never returns at all."""
    from hydrosnooze.scheduler import WAKE_BLASTER_BEFORE

    service.schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[SleepStage(Stage.DEEP, 240, 26), SleepStage(Stage.REM, 240, 27)],
    )
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    starts = plan.precool_at or plan.bedtime_at
    service.clock.jump_to(starts - WAKE_BLASTER_BEFORE + timedelta(minutes=1))

    first = service.scheduler.due(service.schedule, service.clock.now())
    assert first is not None and first.kind == "wake_blaster"
    service.scheduler.fired.mark(first)

    again = service.scheduler.due(service.schedule, service.clock.now())
    assert again is None or again.kind != "wake_blaster", "offered twice in one night"


@pytest.mark.asyncio
async def test_it_never_fires_in_front_of_a_rehearsal(service):
    """A rehearsal compresses the night into minutes, so its first press is
    seconds away. Taking the board off the network then would sabotage the one
    test of the thing this precaution protects."""
    from hydrosnooze.models import rehearsal_plan

    service.scheduler.rehearsal = rehearsal_plan(
        service.schedule.stages,
        service.schedule.cooling_speed,
        now=service.clock.now(),
        total_seconds=300,
        bed_c=26.0,
    )
    for _ in range(4):
        job = service.scheduler.due(service.schedule, service.clock.now())
        assert job is None or job.kind != "wake_blaster"
        if job is not None:
            service.scheduler.fired.mark(job)
        service.clock.advance(timedelta(seconds=20))


@pytest.mark.asyncio
async def test_a_board_that_will_not_restart_does_not_cost_the_night(service):
    """A precaution that can fail the schedule is worse than no precaution. A
    board that refuses to restart may be working perfectly."""

    async def refuse():
        raise RuntimeError("no answer")

    service.transmitter.reboot = refuse
    assert await service._wake_blaster() is True

    said = [e for e in service.events.recent(20) if e.kind == "blaster"]
    assert said and "carrying on" in said[0].message.lower()
