"""What the scheduler remembers about which jobs ran, and for which night.

Six findings from the review on 22 September turned out to be one design
choice. Each job kept a single mark stamped with an exact wake time, and:

- tonight's first stage overwrote last night's, so Autopilot described a perfect
  night as missed from the moment the next one began
- Sleep in moves the wake time, so every stage that had already run stopped
  matching and was reported missed at 3am
- a rehearsal had to wipe every mark to avoid colliding with the real night,
  and that wiped the real night too
- the simulator replaced the whole object and lost the hook that saves it
- a stage cancelled by switching automation off looked exactly like a missed one
- and switching automation off stopped the jobs but not the sampling beat, which
  carried on setting each new stage's temperature regardless

Marks are keyed by the night's wake date now, rehearsals by their own id, with a
third kind for cancelled. A night keeps its identity whatever happens to its
edges, and the table stays bounded at two nights and one rehearsal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Power, Schedule, SleepStage, Stage
from hydrosnooze.scheduler import Job, Scheduler
from hydrosnooze.service import Service

EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def a_schedule() -> Schedule:
    return Schedule(
        wake_time=time(6, 30),
        bed_time=time(22, 30),
        days_of_week=EVERY_DAY,
        stages=[
            SleepStage(Stage.DRIFT, 35, 27),
            SleepStage(Stage.DEEP, 205, 24),
            SleepStage(Stage.REM, 200, 26),
            SleepStage(Stage.WAKE, 40, 28),
        ],
    )


def all_stage_keys(plan) -> set[str]:
    return {f"stage:{s.stage.value}" for s in plan.steps}


# --- Last night is still last night once tonight begins -------------------------


def test_last_nights_marks_survive_tonight_starting():
    sched, schedule = Scheduler(), a_schedule()
    last = sched.plan_in_progress(schedule, datetime(2026, 9, 7, 23, 0))
    for step in last.steps:
        sched.fired.mark(Job("stage", last, step))

    tonight = sched.plan_in_progress(schedule, datetime(2026, 9, 8, 23, 0))
    sched.fired.mark(Job("stage", tonight, tonight.steps[0]))

    assert sched.fired.keys_for(last) == all_stage_keys(last)
    assert sched.fired.keys_for(tonight) == {"stage:drift"}


def test_the_table_stays_bounded_across_a_month_of_nights():
    """Two nights, because the morning report and Autopilot need last night
    while tonight is running. Not more: this lives on an SD card."""
    sched, schedule = Scheduler(), a_schedule()
    for day in range(1, 31):
        plan = sched.plan_in_progress(schedule, datetime(2026, 9, day, 23, 0))
        for step in plan.steps:
            sched.fired.mark(Job("stage", plan, step))
        sched.fired.mark(Job("power_off", plan))
    assert len(sched.fired.done) <= 2 * (len(plan.steps) + 4)


# --- A night keeps its identity when its edges move -----------------------------


def test_sleeping_in_does_not_unmark_what_already_ran():
    sched, schedule = Scheduler(), a_schedule()
    plan = sched.plan_in_progress(schedule, datetime(2026, 9, 7, 23, 0))
    sched.fired.mark(Job("stage", plan, plan.steps[0]))

    later = replace(plan, wake_at=plan.wake_at + timedelta(minutes=15))
    assert sched.fired.has_fired(Job("stage", later, later.steps[0]))


@pytest.fixture
def service(tmp_path):
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime(2026, 9, 8, 3, 0)),
        echo=False,
    )
    svc.schedule = a_schedule()
    svc._set_state(power=Power.ON)
    yield svc
    svc.db.close()


def ran_so_far(service: Service) -> None:
    """Mark every job whose moment has passed, as a real night would have."""
    now = service.clock.now()
    plan = service.scheduler.plan_in_progress(service.schedule, now)
    for step in plan.steps:
        if step.starts_at <= now:
            service.scheduler.fired.mark(Job("stage", plan, step))


@pytest.mark.asyncio
async def test_sleeping_in_at_three_does_not_ring_two_alarms(service):
    """Reproduced in review: Drift and Deep reported missed, at error level,
    pushed to the phone, for a night that was going perfectly."""
    ran_so_far(service)
    service.shift_tonight(wake_minutes=15)

    assert service.scheduler.missed(service.schedule, service.clock.now()) == []


# --- A rehearsal is its own night -----------------------------------------------


@pytest.mark.asyncio
async def test_a_rehearsal_leaves_the_real_night_alone(service):
    """A test run at 07:00, after a 06:30 wake and inside the switch-off window.
    It used to clear every mark, and on the way out the real night reported
    four missed stages, a second morning report and a second switch-off."""
    service.clock.jump_to(datetime(2026, 9, 8, 7, 0))
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    for step in plan.steps:
        service.scheduler.fired.mark(Job("stage", plan, step))
    service.scheduler.fired.mark(Job("power_off", plan))
    service.scheduler.fired.mark(Job("report", plan))

    await service.start_rehearsal(120)
    await service.stop_rehearsal(power_off=False)

    now = service.clock.now()
    assert service.scheduler.missed(service.schedule, now) == []
    assert service.scheduler.due(service.schedule, now) is None


@pytest.mark.asyncio
async def test_a_second_rehearsal_is_not_skipped_as_already_run(service):
    """Why clearing existed in the first place, and still true without it."""
    first = await service.start_rehearsal(120)
    service.scheduler.fired.mark(Job("stage", first, first.steps[0]))
    await service.stop_rehearsal(power_off=False)

    service.clock.advance(timedelta(minutes=5))
    second = await service.start_rehearsal(120)
    assert not service.scheduler.fired.has_fired(Job("stage", second, second.steps[0]))
    await service.stop_rehearsal(power_off=False)


# --- The simulator keeps saving -------------------------------------------------


def test_jumping_the_simulator_clock_keeps_the_marks_saved(service):
    service.sim_jump_to(datetime(2026, 9, 8, 23, 0))
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    service.scheduler.fired.mark(Job("precool", plan))

    assert service.db.fired_marks(), "marked in memory and never written down"


# --- Switching automation off cancels, it does not miss -------------------------


@pytest.mark.asyncio
async def test_a_stage_cancelled_by_switching_off_is_not_an_alarm(service):
    """Reproduced in review: automation off at 02:00, and each later stage
    rang 'Missed the REM stage' at error level as its window closed."""
    service.clock.jump_to(datetime(2026, 9, 8, 2, 0))  # in Deep
    ran_so_far(service)
    service.schedule = replace(service.schedule, enabled=False)

    service.clock.jump_to(datetime(2026, 9, 8, 3, 0))  # REM's window has closed
    await service._tick()

    errors = [e for e in service.events.recent(50) if e.level == "error"]
    assert errors == []
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert "stage:rem" in service.scheduler.fired.cancelled_for(plan)
    assert "stage:rem" not in service.scheduler.fired.keys_for(plan)


@pytest.mark.asyncio
async def test_the_report_says_cancelled_rather_than_missed(service):
    from hydrosnooze import report

    service.clock.jump_to(datetime(2026, 9, 8, 2, 0))
    ran_so_far(service)
    service.schedule = replace(service.schedule, enabled=False)
    # Past both remaining windows, as the report always is when it is built.
    for hour, minute in ((3, 0), (6, 15)):
        service.clock.jump_to(datetime(2026, 9, 8, hour, minute))
        await service._tick()

    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    text, level = report.stages_line(
        plan,
        service.scheduler.fired.keys_for(plan),
        service.scheduler.fired.cancelled_for(plan),
    )
    assert text == "2 of 4 stages landed. Cancelled, as asked: REM, Wake."
    assert level == "info"


@pytest.mark.asyncio
async def test_switching_off_stops_the_sampling_beat_driving_stages(service):
    """Reproduced in review: automation off during Deep, and when REM began the
    sampling beat set REM's temperature anyway."""
    service.clock.jump_to(datetime(2026, 9, 8, 2, 0))  # in Deep
    ran_so_far(service)
    service._set_state(power=Power.ON, assumed_target_c=24)
    service.schedule = replace(service.schedule, enabled=False)

    service.clock.jump_to(datetime(2026, 9, 8, 3, 0))  # REM has begun
    before = service.transmitter.presses_sent
    await service._follow_the_plan(loud=True)

    assert service.transmitter.presses_sent == before
    assert service.state.assumed_target_c == 24
    assert service.scheduler.stage_now(service.schedule, service.clock.now()) is None


# --- The marks already on the Pi -------------------------------------------------


def test_marks_written_the_old_way_still_count_after_the_upgrade(tmp_path):
    """The first restart after this deploys reads marks keyed the old way, just
    `stage:drift` stamped with a wake time. If those stopped counting, a deploy
    mid-night would report every stage that ran as missed, which is the exact
    false alarm the stored marks were added to prevent."""
    from hydrosnooze.db import Database

    schedule = a_schedule()
    plan = Scheduler().plan_in_progress(schedule, datetime(2026, 9, 7, 23, 0))
    db = Database(str(tmp_path / "old.db"))
    db.set_fired_marks({"stage:drift": plan.wake_at, "missed:stage:deep": plan.wake_at})

    sched = Scheduler()
    sched.fired.store = db.set_fired_marks
    sched.fired.load(db.fired_marks())

    assert sched.fired.keys_for(plan) == {"stage:drift"}
    assert sched.fired.gave_up_on(Job("stage", plan, plan.steps[1]))
    # And rewritten in the new shape, so the migration happens once.
    assert all("|" in key for key in db.fired_marks())
    db.close()
