"""Getting the bed ready before the night starts, and the whole night after it.

A cooler cannot warm a bed. If the first stage is above whatever the bed is
resting at, only warming mode gets there, and only down to 25C.

The arming ordering that used to matter here is gone: the unit's own scheduler is
never armed, so cooling and warming can be switched at any point. What replaces it
is the night itself, driven stage by stage.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Mode, Schedule, SleepStage, Stage
from hydrosnooze.service import Service


@pytest.fixture
def service(tmp_path):
    settings = Settings(db_path=str(tmp_path / "test.db"), sim_speed=1.0)
    clock = VirtualClock(datetime(2026, 9, 7, 21, 0))
    svc = Service(settings, clock=clock, echo=False)
    yield svc
    svc.db.close()


def _schedule(**kwargs) -> Schedule:
    base = dict(
        wake_time=time(6, 30),
        days_of_week=[0, 1, 2, 3, 4],
        stages=[
            SleepStage(Stage.DEEP, 240, 17),
            SleepStage(Stage.REM, 210, 20),
            SleepStage(Stage.WAKE, 30, 26),
        ],
    )
    base.update(kwargs)
    return Schedule(**base)  # type: ignore[arg-type]


def _warm_first(**kwargs) -> Schedule:
    return _schedule(
        stages=[SleepStage(Stage.DEEP, 240, 26), SleepStage(Stage.WAKE, 30, 28)],
        **kwargs,
    )


# --- Deciding how the bed gets ready ------------------------------------------
#
# Never a setting. The bed starts at room temperature, the first stage says where
# it has to be, and the gap between them decides the mode and the head start.


@pytest.mark.parametrize("temp", [15, 17, 18])
def test_a_cold_first_stage_pre_cools_in_turbo(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning
    assert pre.mode is Mode.TURBO
    assert "Cooling the bed" in pre.reason


@pytest.mark.parametrize("temp", [25, 28, 30])
def test_a_warm_first_stage_pre_heats(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning
    assert pre.mode is Mode.WARMING
    assert "Warming the bed" in pre.reason


@pytest.mark.parametrize("temp", [22, 23, 24])
def test_warmer_than_the_room_but_below_warming_floor_does_nothing(temp):
    """The one gap nothing can close. The bed has to warm, warming mode cannot
    express a number that low, and running the cooler at it would be worse."""
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning
    assert pre.mode is None
    assert not pre.runs
    assert "25C" in pre.reason


@pytest.mark.parametrize("temp", [19, 20, 21])
def test_a_first_stage_at_room_temperature_does_nothing(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning
    assert pre.mode is None
    assert "already sits" in pre.reason


def test_the_head_start_grows_with_the_distance():
    """Not a fixed thirty minutes any more. Three degrees is a shorter job than
    ten, and starting an hour early for three degrees just wastes power."""
    near = _schedule(stages=[SleepStage(Stage.DEEP, 240, 18)]).preconditioning
    far = _schedule(stages=[SleepStage(Stage.DEEP, 240, 15)]).preconditioning
    assert far.lead_minutes > near.lead_minutes
    assert 10 <= near.lead_minutes <= 90
    assert 10 <= far.lead_minutes <= 90


def test_nothing_to_do_means_no_pre_conditioning_in_the_plan():
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 20)])
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is None
    assert plan.starts_at == plan.bedtime_at


def test_the_plan_starts_exactly_its_head_start_before_bedtime():
    schedule = _schedule()
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    lead = timedelta(minutes=schedule.preconditioning.lead_minutes)
    assert plan.precool_at == plan.bedtime_at - lead


# --- A whole night ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_night_cools_then_heats(service):
    """The thing the unit's own scheduler made impossible.

    While its Smart Sleep Schedule runs it refuses to switch between cooling and
    warming, which capped every night at "somewhere at or below the bedroom".
    """
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.mode is Mode.TURBO
    assert service.unit.target == 17

    seen = []
    for step in plan.steps:
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)
        seen.append((step.label, service.unit.mode, service.unit.target))

    assert seen == [
        ("Deep", Mode.QUIET, 17),
        ("REM", Mode.QUIET, 20),
        ("Wake", Mode.WARMING, 26),
    ]


@pytest.mark.asyncio
async def test_the_night_ends_with_the_unit_switched_off(service):
    """Not optional. Nothing else turns it off now."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.powered

    service.clock.jump_to(plan.wake_at)
    await service._run_power_off(plan)
    assert not service.unit.powered


@pytest.mark.asyncio
async def test_a_stage_powers_the_unit_on_if_it_is_off(service):
    # Whether it was never started, or switched itself off, the night should not
    # silently continue against a dead unit.
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    step = plan.steps[1]
    service.clock.jump_to(step.starts_at)
    assert not service.unit.powered

    await service._run_stage(plan, step)
    assert service.unit.powered
    assert service.unit.target == step.temp_c


@pytest.mark.asyncio
async def test_a_night_never_touches_the_mute_button(service):
    """The unit remembers whether it is muted, so this is never automatic.

    Sending it at each power on would unmute it every other night, and the press
    that unmuted it would beep.
    """
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    for step in plan.steps:
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)

    assert not any("mute" in line for line in service.transmitter.lines)
    assert not service.unit.muted, "left exactly as it was found"


@pytest.mark.asyncio
async def test_pre_heating_leaves_the_bed_warm_before_a_warm_first_stage(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 26


@pytest.mark.asyncio
async def test_a_gap_nothing_can_close_sends_no_presses_and_says_why(service):
    """First stage 23C, room 20C. The bed has to warm and nothing can warm it to
    23C, so the unit stays off rather than running a cooler at a bed needing heat."""
    service.schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 23)])
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is None

    before = len(list(service.transmitter.lines))
    await service._run_precool(plan)

    assert len(list(service.transmitter.lines)) == before, "no presses at all"
    assert any("25C" in e.message for e in service.events.recent(50))


# --- Saying so when it achieved nothing ---------------------------------------


@pytest.mark.asyncio
async def test_a_pre_conditioning_run_that_never_drew_power_is_reported(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 3.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert any("never drew more than" in m for m in messages)


@pytest.mark.asyncio
async def test_a_working_pre_conditioning_run_is_not_reported(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 290.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert not any("never drew more than" in m for m in messages)


@pytest.mark.asyncio
async def test_muting_gives_up_the_target_rather_than_lying_about_it(service):
    """Mute is the one command that cannot put the temperature back.

    Its wake preamble is two temp_down presses, and whichever of them are not
    swallowed really do lower the target. Every other command rails to a mode's
    floor and counts up afterwards, which absorbs them. This one has nothing to
    count to, so the unit ends up a degree or two below what the app last set,
    and the app has to say it no longer knows rather than keep showing the number.
    """
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()
    await service.set_temperature(20)
    assert service.state.assumed_target_c == 20

    await service.mute()

    assert service.unit.muted
    assert service.unit.target < 20, "the wake presses land on a woken display"
    assert service.state.assumed_target_c is None


# --- The overlap, driven through the unit --------------------------------------
#
# Cooling reaches 15 to 35 and warming reaches 25 to 55, so between 25 and 35 both
# modes can hold the number. Only one of them moves the bed there.


@pytest.mark.asyncio
async def test_a_stage_that_drops_into_the_overlap_switches_the_unit_to_cooling(service):
    """Deep at 30C then REM at 25C. Warming would set 25 and then do nothing at
    all while the bed coasted down on its own for the next three hours."""
    service.schedule = _schedule(
        stages=[
            SleepStage(Stage.DEEP, 240, 30),
            SleepStage(Stage.REM, 210, 25),
            SleepStage(Stage.WAKE, 30, 26),
        ]
    )
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    deep, rem, wake = plan.steps

    service.clock.jump_to(deep.starts_at)
    await service._run_stage(plan, deep)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 30

    service.clock.jump_to(rem.starts_at)
    await service._run_stage(plan, rem)
    assert service.unit.mode.is_cooling, "the bed has to come down, so the unit has to cool"
    assert service.unit.target == 25

    # And back up again for the last half hour, which only warming can do.
    service.clock.jump_to(wake.starts_at)
    await service._run_stage(plan, wake)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 26


@pytest.mark.asyncio
async def test_setting_a_lower_temperature_by_hand_cools_too(service):
    """The Now control has no stage before it, so it goes on the last target we
    set. Not a reading, but it is the number we last asked the unit to hold."""
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()

    await service.set_temperature(30)
    assert service.unit.mode is Mode.WARMING

    await service.set_temperature(25)
    assert service.unit.mode.is_cooling
    assert service.unit.target == 25


@pytest.mark.asyncio
async def test_setting_a_higher_temperature_by_hand_still_warms(service):
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()

    await service.set_temperature(20)
    assert service.unit.mode.is_cooling

    await service.set_temperature(28)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 28
