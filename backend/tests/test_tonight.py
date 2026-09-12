"""Tonight only: the exception to the routine, and nothing beyond it.

The saved Schedule is the nights you usually have. Tonight is the one you are
actually having, and it expires with the morning it belongs to.

They used to be the same object. Reaching for the temperature at 2am wrote the
new number straight into the routine, so every experiment cost a permanent
change, silently, and undoing it meant remembering what the number had been. "I
was cold once" became "this is how I sleep".

The engineering constraint that shapes all of it: **a temporary change may never
move the shutdown deadline.** Bedtime, the alarm and the switch-off are settled
when a night starts. How warm the bed is for the next half hour has no business
touching when it ends, so a nudge holds degrees and nothing else.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import NUDGE_LIMIT_C, Mode, Schedule, SleepStage, Stage, Tonight
from hydrosnooze.service import Service

# A Saturday evening, before a Sunday morning alarm.
NOW = datetime(2026, 9, 12, 20, 0)
TONIGHT = date(2026, 9, 13)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    )
    svc.load_tonight()
    yield svc
    svc.db.close()


def usual(service):
    return [s.temp_c for s in service.schedule.stages]


def running(service):
    return [s.temp_c for s in service.tonight_now().stages]


# --- The lens -------------------------------------------------------------------


async def test_with_nothing_set_tonight_is_just_the_routine(service):
    assert running(service) == usual(service) == [19, 22, 26]
    assert service.scheduler.tonight is None


async def test_a_stage_set_for_tonight_leaves_the_routine_alone(service):
    service.set_stage_tonight(Stage.DEEP, 17)

    assert running(service) == [17, 22, 26]
    assert usual(service) == [19, 22, 26], "next week is unchanged"


async def test_it_expires_by_the_calendar_rather_than_by_tidying_up(service):
    """Nothing has to remember to clear it. A row for another night is spent."""
    service.set_stage_tonight(Stage.DEEP, 17)
    assert service.db.load_tonight(TONIGHT) is not None
    assert service.db.load_tonight(TONIGHT + timedelta(days=1)) is None


async def test_it_survives_a_restart(service, tmp_path):
    """The Pi restarts routinely, and forgetting tonight at 3am would put the bed
    back to a number already rejected."""
    service.set_stage_tonight(Stage.REM, 24)
    service.db.close()

    again = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    again.schedule = service.schedule
    again.load_tonight()
    assert [s.temp_c for s in again.tonight_now().stages] == [19, 24, 26]
    again.db.close()


# --- The shutdown deadline, which is the constraint on all of this ---------------


async def test_a_nudge_cannot_move_the_switch_off(service):
    """The whole reason a nudge holds degrees and nothing else."""
    before = service.scheduler.night_date(service.schedule, NOW)
    was = service.tonight_now().wake_time

    service.nudge_tonight(-1)

    assert service.tonight_now().wake_time == was
    assert service.scheduler.night_date(service.schedule, NOW) == before


async def test_a_nudge_lapses_on_its_own(service):
    service.nudge_tonight(-1, minutes=30)
    tonight = service.scheduler.tonight

    assert tonight.nudge_at(NOW + timedelta(minutes=29)) == -1
    assert tonight.nudge_at(NOW + timedelta(minutes=31)) == 0, "back to the plan"


async def test_a_nudge_applies_to_what_the_plan_asks_for(service):
    service.nudge_tonight(-1)
    assert service._nudged(22, Mode.QUIET) == 21

    service.clock.advance(timedelta(hours=1))
    assert service._nudged(22, Mode.QUIET) == 22, "and stops when it lapses"


async def test_a_nudge_is_one_degree_and_does_not_stack(service):
    """One degree, one period. A nudge that can be tapped up to four degrees is a
    temperature control with a timer on it, and there is already a temperature
    control."""
    service.nudge_tonight(-40)
    assert service.scheduler.tonight.nudge_c == -NUDGE_LIMIT_C == -1

    service.nudge_tonight(-1)
    assert service.scheduler.tonight.nudge_c == -1, "a second one does not double it"


async def test_a_nudge_respects_the_safety_cap(service):
    service.nudge_tonight(NUDGE_LIMIT_C)
    assert service._nudged(service.settings.max_temperature_c, Mode.WARMING) <= (
        service.settings.max_temperature_c
    )


# --- Moving the edges of the night ------------------------------------------------


async def test_sleeping_in_moves_the_alarm_and_the_switch_off_together(service):
    service.shift_tonight(wake_minutes=90)

    assert service.tonight_now().wake_time == time(9, 0)
    assert service.schedule.wake_time == time(7, 30), "the routine is untouched"

    plan = service.scheduler.plan_in_progress(service.schedule, NOW)
    assert plan.wake_at.time() == time(9, 0), "and the unit switches off at the new alarm"


async def test_sleeping_in_stretches_the_stages_rather_than_dropping_one(service):
    before = sum(s.duration_minutes for s in service.tonight_now().stages)
    service.shift_tonight(wake_minutes=90)
    after = sum(s.duration_minutes for s in service.tonight_now().stages)

    assert after == before + 90
    assert len(service.tonight_now().stages) == 3


async def test_going_to_bed_early_brings_the_preparation_forward(service):
    was = service.scheduler.plan_in_progress(service.schedule, NOW).bedtime_at

    service.shift_tonight(bed_minutes=-60)

    now = service.scheduler.plan_in_progress(service.schedule, NOW).bedtime_at
    assert now == was - timedelta(hours=1)
    assert service.schedule.bed_time == time(22, 30), "the routine is untouched"


# --- Skipping -------------------------------------------------------------------


async def test_skipping_tonight_leaves_the_week_alone(service):
    """It moves on to the next night rather than stopping: the routine is intact,
    this one night is simply not one of its nights."""
    service.skip_tonight()

    plan = service.scheduler.plan_in_progress(service.schedule, NOW)
    assert plan is not None and plan.wake_at.date() != TONIGHT, "not tonight"
    assert service.scheduler.due(service.schedule, NOW) is None, "and nothing runs tonight"
    assert service.schedule.days_of_week == [0, 1, 2, 3, 4, 5, 6], "still every night"


async def test_a_skipped_night_is_not_reported_on_in_the_morning(service):
    service.skip_tonight()
    after = datetime(2026, 9, 13, 10, 0)
    assert service.scheduler.last_finished(service.schedule, after) != TONIGHT


async def test_tomorrow_runs_as_usual_after_a_skip(service):
    service.skip_tonight()
    tomorrow_evening = datetime(2026, 9, 13, 20, 0)
    plan = service.scheduler.plan_in_progress(service.schedule, tomorrow_evening)
    assert plan is not None and plan.wake_at.date() == date(2026, 9, 14)


# --- Putting it back --------------------------------------------------------------


async def test_clearing_it_puts_the_night_back_to_the_routine(service):
    service.set_stage_tonight(Stage.DEEP, 17)
    service.shift_tonight(wake_minutes=60)

    service.clear_tonight()

    assert running(service) == [19, 22, 26]
    assert service.tonight_now().wake_time == time(7, 30)
    assert service.db.load_tonight(TONIGHT) is None


async def test_saving_it_as_a_preference_is_the_deliberate_one(service):
    service.set_stage_tonight(Stage.DEEP, 17)
    assert usual(service) == [19, 22, 26], "not yet"

    service._adopt_into_running_stage(Stage.DEEP, 17)
    assert usual(service) == [17, 22, 26], "now"


# --- Which controls make sense right now ------------------------------------------


async def test_the_controls_follow_where_you_are_in_the_night(service):
    """They do not share one window. Shaping a night happens before it starts and
    nudging one happens from inside it, so offering all six all the time would put
    "going to bed early" in front of somebody already in bed."""
    plan = service.scheduler.plan_in_progress(service.schedule, NOW)

    for when, expected in [
        (plan.starts_at - timedelta(hours=9), "none"),
        (plan.starts_at - timedelta(hours=2), "evening"),
        (plan.starts_at + timedelta(minutes=5), "running"),
        (plan.wake_at - timedelta(hours=1), "running"),
        (plan.wake_at + timedelta(minutes=5), "after"),
    ]:
        service.clock.jump_to(when)
        assert service.tonight_phase() == expected, f"at {when:%H:%M}"


async def test_nothing_is_offered_while_automation_is_off(service):
    from dataclasses import replace as _replace

    service.schedule = _replace(service.schedule, enabled=False)
    service.clock.jump_to(NOW)
    assert service.tonight_phase() == "none"
