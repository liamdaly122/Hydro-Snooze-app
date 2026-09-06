"""Pre-heating, and the one mistake that must never ship.

A cooler cannot warm a bed. When phase 1 is above whatever the bed is resting at,
pre-cooling achieves nothing and the app would otherwise report success.

Warming mode only goes down to 25C, so this is a narrow fix, and outside that band
the honest answer is to say so rather than pretend.

The dangerous part is the ordering. Cooling and warming cannot be switched once a
schedule is running, so a pre-heated bed must be put back into the night's cooling
mode BEFORE arming. Getting that backwards would arm the unit into a night of
warming.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Mode, Precondition, Schedule
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
        mode=Mode.QUIET,
        phase1_temp_c=26,
        phase2_temp_c=17,
        phase3_temp_c=21,
        precondition=Precondition.WARM,
    )
    base.update(kwargs)
    return Schedule(**base)  # type: ignore[arg-type]


# --- What warming can and cannot express --------------------------------------


@pytest.mark.parametrize("temp", [15, 19, 24])
def test_pre_heating_below_twenty_five_is_refused(temp):
    # Warming mode's range is 25 to 55. There is no press sequence that warms a
    # bed to 19C, so this is refused rather than silently downgraded.
    problem = _schedule(phase1_temp_c=temp).precondition_problem()
    assert problem is not None
    assert "25C" in problem


@pytest.mark.parametrize("temp", [25, 28, 30])
def test_pre_heating_inside_the_warming_range_is_allowed(temp):
    assert _schedule(phase1_temp_c=temp).precondition_problem() is None


def test_pre_cooling_is_never_refused():
    # Cooling covers 15 to 35, so there is nothing to complain about.
    assert _schedule(phase1_temp_c=19, precondition=Precondition.COOL).precondition_problem() is None


def test_the_default_is_unchanged_behaviour():
    assert Schedule().precondition is Precondition.COOL
    assert Schedule().precondition_mode is Mode.TURBO


# --- The ordering that matters ------------------------------------------------


async def test_pre_heating_then_arming_leaves_the_unit_cooling(service):
    """The one that must never regress.

    Cooling and warming cannot be switched once a schedule is running. If the
    night mode were applied after arming, the unit would spend the night warming
    a bed that was asked to be cooled.
    """
    service.schedule = _schedule(mode=Mode.QUIET, phase1_temp_c=26)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    await service._run_precool(plan)
    assert service.unit.mode is Mode.WARMING, "pre-conditioning should warm the bed"
    assert service.unit.target == 26

    service.clock.jump_to(plan.arm_at)
    await service._run_arm(plan)

    assert service.unit.mode is Mode.QUIET, "the night must run in the cooling mode, not warming"
    assert service.unit.schedule_running()


@pytest.mark.parametrize("night_mode", [Mode.QUIET, Mode.STANDARD, Mode.TURBO])
async def test_pre_heating_reaches_every_cooling_mode_before_arming(service, night_mode):
    service.schedule = _schedule(mode=night_mode, phase1_temp_c=27)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    await service._run_precool(plan)
    service.clock.jump_to(plan.arm_at)
    await service._run_arm(plan)
    assert service.unit.mode is night_mode
    assert service.unit.schedule_running()


async def test_the_mode_is_set_before_arming_not_after(service):
    # Checked in the press log rather than only in the end state, because the end
    # state would look identical if the order were wrong and the unit happened to
    # allow it.
    service.schedule = _schedule(mode=Mode.QUIET, phase1_temp_c=26)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    await service._run_precool(plan)
    service.clock.jump_to(plan.arm_at)
    service.transmitter.lines.clear()
    await service._run_arm(plan)

    log = list(service.transmitter.lines)
    set_mode_at = next(i for i, line in enumerate(log) if "set_mode(quiet)" in line)
    arm_at = next(i for i, line in enumerate(log) if "arm_schedule()" in line)
    assert set_mode_at < arm_at, "the cooling mode must be restored before the schedule is armed"
    assert not any("set_cooling_speed" in line for line in log), "no post-arm drop after pre-heating"


async def test_pre_cooling_still_drops_the_speed_after_arming(service):
    # The original path, unchanged: pre-cool in Turbo, arm, then drop to the
    # quieter night speed while the display is still awake.
    service.schedule = _schedule(
        mode=Mode.QUIET, phase1_temp_c=19, precondition=Precondition.COOL
    )
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    await service._run_precool(plan)
    assert service.unit.mode is Mode.TURBO

    service.clock.jump_to(plan.arm_at)
    service.transmitter.lines.clear()
    await service._run_arm(plan)

    log = list(service.transmitter.lines)
    arm_at = next(i for i, line in enumerate(log) if "arm_schedule()" in line)
    drop_at = next(i for i, line in enumerate(log) if "set_cooling_speed" in line)
    assert drop_at > arm_at, "the speed drop belongs after arming, while the display is awake"
    assert service.unit.mode is Mode.QUIET
    assert service.unit.schedule_running()


async def test_an_impossible_pre_heat_falls_back_to_cooling_and_says_so(service):
    # Should be unreachable through the API, but the nightly job must not act on a
    # setting it cannot honour if one ever gets in.
    service.schedule = _schedule(phase1_temp_c=19, precondition=Precondition.WARM)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    await service._run_precool(plan)

    assert service.unit.mode is Mode.TURBO, "fell back to pre-cooling"
    warnings = [e for e in service.events.recent(50) if e.level == "warning"]
    assert any("25C" in e.message for e in warnings)


# --- Saying so when it achieved nothing ---------------------------------------


async def test_a_pre_conditioning_run_that_never_drew_power_is_reported(service):
    service.schedule = _schedule(phase1_temp_c=26)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    # A unit sitting at idle for the whole pre-conditioning window: it was never
    # working, so the bed was already past the target.
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 3.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert any("never drew more than" in m for m in messages)


async def test_a_working_pre_conditioning_run_is_not_reported(service):
    service.schedule = _schedule(phase1_temp_c=26)
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 290.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert not any("never drew more than" in m for m in messages)
