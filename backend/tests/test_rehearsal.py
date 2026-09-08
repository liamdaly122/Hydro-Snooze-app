"""A whole night, compressed, so the hardware can be trusted before it is relied on.

The one thing neither the simulator nor 237 tests can answer is whether a stage
boundary really lands on a unit that is actually there. A rehearsal answers it by
running the real thing on a short clock, so what is tested here is mostly that it
is not a special case: the same scheduler, the same jobs, the same order.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.models import (
    MIN_REHEARSAL_STAGE_S,
    Mode,
    Schedule,
    SleepStage,
    Stage,
    rehearsal_plan,
)
from hydrosnooze.scheduler import Scheduler

NOW = datetime(2026, 9, 8, 14, 0)


@pytest.fixture
def schedule() -> Schedule:
    return Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])


def plan(schedule: Schedule, seconds: int = 300):
    return rehearsal_plan(
        schedule.stages, schedule.cooling_speed, now=NOW, total_seconds=seconds
    )


# --- The compressed night ------------------------------------------------------


def test_it_rehearses_every_stage_in_order(schedule):
    steps = plan(schedule).steps
    assert [s.stage for s in steps] == [Stage.DEEP, Stage.REM, Stage.WAKE]


def test_the_temperatures_are_tonight_s_own(schedule):
    """A generic night would prove nothing about the night that will run."""
    assert [s.temp_c for s in plan(schedule).steps] == [s.temp_c for s in schedule.stages]


def test_the_modes_are_chosen_the_same_way_a_real_night_chooses_them(schedule):
    """The direction rule is the part most worth exercising on real hardware, so
    it has to be the real one rather than a simplification."""
    schedule.stages = [
        SleepStage(Stage.DEEP, 240, 30),
        SleepStage(Stage.REM, 240, 25),
        SleepStage(Stage.WAKE, 30, 40),
    ]
    modes = [s.mode for s in plan(schedule).steps]
    # 30 down to 25 has to cool even though 25 is inside the warming range.
    assert modes[1].is_cooling
    assert modes[2] is Mode.WARMING


def test_it_finishes_within_the_time_asked_for(schedule):
    p = plan(schedule, seconds=300)
    assert timedelta(seconds=280) <= p.wake_at - p.bedtime_at <= timedelta(seconds=320)


def test_a_stage_is_never_shorter_than_its_own_presses(schedule):
    """Thirty-five presses take about ten seconds. A stage shorter than that
    would still be sending the last one when the next boundary arrived."""
    for step in plan(schedule, seconds=120).steps:
        assert (step.ends_at - step.starts_at).total_seconds() >= MIN_REHEARSAL_STAGE_S


def test_asking_for_something_too_short_stretches_rather_than_overlaps(schedule):
    p = plan(schedule, seconds=1)
    assert (p.wake_at - p.bedtime_at).total_seconds() >= MIN_REHEARSAL_STAGE_S * 3


def test_the_proportions_of_the_real_night_are_kept(schedule):
    """Deep is eight times Wake tonight, so it should be roughly eight times in
    the rehearsal too, not an equal third."""
    steps = plan(schedule, seconds=900).steps
    deep, wake = (steps[0].ends_at - steps[0].starts_at), (steps[2].ends_at - steps[2].starts_at)
    assert deep > wake * 4


def test_preconditioning_still_runs_and_still_decides_for_itself(schedule):
    p = plan(schedule)
    assert p.precool_at is not None
    assert p.preconditioning.runs


def test_a_night_with_no_stages_is_refused(schedule):
    schedule.stages = []
    with pytest.raises(ValueError):
        plan(schedule)


# --- That it is the same scheduler, not a parallel one -------------------------


def test_the_rehearsal_replaces_the_real_night_while_it_runs(schedule):
    sched = Scheduler()
    sched.rehearsal = plan(schedule)
    assert sched.plan_in_progress(schedule, NOW) is sched.rehearsal


def test_clearing_it_puts_the_real_night_straight_back(schedule):
    sched = Scheduler()
    sched.rehearsal = plan(schedule)
    sched.rehearsal = None
    back = sched.plan_in_progress(schedule, NOW)
    assert back is not None and back.wake_at.time() == time(6, 30)


def test_every_job_of_a_rehearsal_fires_in_order(schedule):
    """The real point. Walk a compressed night second by second through the
    ordinary due() and check the same five things happen as on a real night."""
    sched = Scheduler()
    p = plan(schedule, seconds=300)
    sched.rehearsal = p

    fired = []
    now = NOW
    end = p.wake_at + timedelta(seconds=5)
    while now <= end:
        job = sched.due(schedule, now)
        if job is not None:
            sched.fired.mark(job)
            fired.append(job.key)
        now += timedelta(seconds=1)

    assert fired == ["precool", "stage:deep", "stage:rem", "stage:wake", "power_off"]


def test_nothing_is_missed_during_a_rehearsal(schedule):
    """A stage that fires but is reported missed would be alarming at 2am, and
    the rehearsal's windows are far shorter than STAGE_GRACE."""
    sched = Scheduler()
    p = plan(schedule, seconds=300)
    sched.rehearsal = p

    now = NOW
    while now <= p.wake_at:
        job = sched.due(schedule, now)
        if job is not None:
            sched.fired.mark(job)
        assert sched.missed(schedule, now) == []
        now += timedelta(seconds=1)


def test_a_rehearsal_always_ends_by_switching_the_unit_off(schedule):
    """Not optional. A rehearsal that left the bed running would be worse than
    no rehearsal at all."""
    sched = Scheduler()
    p = plan(schedule, seconds=300)
    sched.rehearsal = p
    job = sched.due(schedule, p.wake_at)
    assert job is not None and job.kind == "power_off"
