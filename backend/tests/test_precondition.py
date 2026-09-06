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
from hydrosnooze.models import Mode, Precondition, Schedule, SleepStage, Stage
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
        precondition=Precondition.COOL,
    )
    base.update(kwargs)
    return Schedule(**base)  # type: ignore[arg-type]


def _warm_first(**kwargs) -> Schedule:
    return _schedule(
        stages=[SleepStage(Stage.DEEP, 240, 26), SleepStage(Stage.WAKE, 30, 28)],
        precondition=Precondition.WARM,
        **kwargs,
    )


# --- What warming can and cannot express --------------------------------------


@pytest.mark.parametrize("temp", [15, 19, 24])
def test_pre_heating_below_twenty_five_is_refused(temp):
    schedule = _schedule(
        stages=[SleepStage(Stage.DEEP, 240, temp)], precondition=Precondition.WARM
    )
    problem = schedule.precondition_problem()
    assert problem is not None
    assert "25C" in problem


@pytest.mark.parametrize("temp", [25, 28, 30])
def test_pre_heating_inside_the_warming_range_is_allowed(temp):
    schedule = _schedule(
        stages=[SleepStage(Stage.DEEP, 240, temp)], precondition=Precondition.WARM
    )
    assert schedule.precondition_problem() is None


def test_pre_cooling_is_never_refused():
    assert _schedule().precondition_problem() is None


def test_the_default_is_unchanged_behaviour():
    assert Schedule().precondition is Precondition.COOL
    assert Schedule().precondition_mode is Mode.TURBO


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
async def test_an_impossible_pre_heat_falls_back_to_cooling_and_says_so(service):
    service.schedule = _schedule(precondition=Precondition.WARM)  # first stage 17C
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)

    assert service.unit.mode is Mode.TURBO
    warnings = [e for e in service.events.recent(50) if e.level == "warning"]
    assert any("25C" in e.message for e in warnings)


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
