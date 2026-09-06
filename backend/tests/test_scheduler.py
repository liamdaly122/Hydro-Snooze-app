"""When each part of the night fires, and just as importantly when it does not.

The unit's own scheduler is no longer used, so nothing runs itself any more. Every
stage boundary is a job, and the one at the end that switches the unit off is not
optional: without it the bed runs all day.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from hydrosnooze.models import Mode, Schedule, SleepStage, Stage
from hydrosnooze.scheduler import Scheduler


@pytest.fixture
def schedule() -> Schedule:
    # Deep 4h at 17C, REM 3h30 at 20C, Wake 30m at 26C, finishing at 06:30.
    # So bedtime is 22:30 and pre-conditioning starts at 22:00.
    return Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])


def _fire(sched: Scheduler, schedule: Schedule, when: datetime):
    job = sched.due(schedule, when)
    if job is not None:
        sched.mark = job
        sched.fired.mark(job)
    return job


def test_nothing_is_due_in_the_afternoon(schedule):
    assert Scheduler().due(schedule, datetime(2026, 9, 7, 15, 0)) is None


def test_pre_conditioning_fires_before_bedtime(schedule):
    job = Scheduler().due(schedule, datetime(2026, 9, 7, 22, 0))
    assert job is not None and job.kind == "precool"


def test_every_stage_fires_at_its_boundary(schedule):
    sched = Scheduler()
    fired = []
    for when in [
        datetime(2026, 9, 7, 22, 0),
        datetime(2026, 9, 7, 22, 30),
        datetime(2026, 9, 8, 2, 30),
        datetime(2026, 9, 8, 6, 0),
        datetime(2026, 9, 8, 6, 30),
    ]:
        job = _fire(sched, schedule, when)
        assert job is not None, when
        fired.append(job.step.stage if job.step else job.kind)
    assert fired == ["precool", Stage.DEEP, Stage.REM, Stage.WAKE, "power_off"]


def test_a_job_only_fires_once(schedule):
    sched = Scheduler()
    _fire(sched, schedule, datetime(2026, 9, 7, 22, 30))
    assert sched.due(schedule, datetime(2026, 9, 7, 22, 35)) is None


def test_a_late_start_jumps_to_the_stage_that_should_be_running(schedule):
    # Booting at 3am should go straight to REM, not walk through Deep first and
    # leave the bed four degrees too cold.
    job = Scheduler().due(schedule, datetime(2026, 9, 8, 2, 35))
    assert job is not None and job.step is not None
    assert job.step.stage is Stage.REM


def test_a_stage_is_not_set_once_it_is_nearly_over(schedule):
    # Twenty minutes late is still worth doing. An hour late is not: the stage is
    # mostly gone and changing the bed then is worse than leaving it.
    assert Scheduler().due(schedule, datetime(2026, 9, 8, 3, 40)) is None


def test_a_missed_stage_is_reported(schedule):
    missed = Scheduler().missed(schedule, datetime(2026, 9, 8, 3, 40))
    assert [m.step.stage for m in missed if m.step] == [Stage.DEEP, Stage.REM]


def test_powering_off_is_not_optional(schedule):
    # Nothing else turns the unit off now. This is the job that matters most.
    sched = Scheduler()
    job = sched.due(schedule, datetime(2026, 9, 8, 6, 30))
    assert job is not None and job.kind == "power_off"


def test_powering_off_has_a_generous_window(schedule):
    # A stage missed by an hour is water under the bridge. A power off missed by
    # an hour is a bed heating all day, so it keeps trying for much longer.
    job = Scheduler().due(schedule, datetime(2026, 9, 8, 8, 0))
    assert job is not None and job.kind == "power_off"


def test_saturday_night_is_skipped_when_sunday_is_not_selected(schedule):
    assert Scheduler().due(schedule, datetime(2026, 9, 12, 22, 30)) is None


def test_sunday_evening_starts_mondays_night(schedule):
    job = Scheduler().due(schedule, datetime(2026, 9, 6, 22, 30))
    assert job is not None and job.step is not None
    assert job.step.stage is Stage.DEEP
    assert job.plan.wake_at == datetime(2026, 9, 7, 6, 30)


def test_a_disabled_schedule_never_fires(schedule):
    schedule.enabled = False
    assert Scheduler().due(schedule, datetime(2026, 9, 7, 22, 30)) is None


def test_a_schedule_with_no_stages_never_fires(schedule):
    schedule.stages = []
    assert Scheduler().due(schedule, datetime(2026, 9, 7, 22, 30)) is None


def test_stage_now_reports_where_the_night_has_got_to(schedule):
    sched = Scheduler()
    assert sched.stage_now(schedule, datetime(2026, 9, 8, 0, 0)).stage is Stage.DEEP
    assert sched.stage_now(schedule, datetime(2026, 9, 8, 4, 0)).stage is Stage.REM
    assert sched.stage_now(schedule, datetime(2026, 9, 7, 15, 0)) is None


def test_a_longer_night_moves_bedtime_earlier(schedule):
    # No fixed 8h30m. Ten hours of stages means lights out at 20:30.
    schedule.stages = [SleepStage(Stage.DEEP, 600, 17)]
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.bedtime_at == datetime(2026, 9, 7, 20, 30)


def test_a_night_that_cools_then_heats_is_allowed(schedule):
    schedule.stages = [SleepStage(Stage.DEEP, 300, 16), SleepStage(Stage.WAKE, 60, 28)]
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.steps[0].mode.is_cooling
    assert plan.steps[1].mode is Mode.WARMING
