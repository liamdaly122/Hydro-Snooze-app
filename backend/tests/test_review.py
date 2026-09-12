"""The seven Codex found in the review of 5ecadeb, as failing tests first.

Kept together rather than scattered into the files they each belong to, because
what they have in common is how they were found: a review of a commit that passed
625 tests and a frontend build. Every one of these is a hole the suite had, and a
file that says so is worth more than seven tests filed quietly under other names.

Three of them are the same mistake in different places: something that decides
what the unit is sent reads the saved schedule when it should read the night
actually being run.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import MIN_RUNS_TO_LEARN, Database
from hydrosnooze.models import (
    DRIFT_MINUTES,
    Mode,
    Schedule,
    SleepStage,
    Stage,
    with_all_stages,
)
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 12, 20, 0)
TONIGHT = date(2026, 9, 13)


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 21),
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    )
    svc.load_tonight()
    yield svc
    svc.db.close()


# --- 1. Skipping a night that has already started -------------------------------


def test_skipping_mid_night_still_switches_the_unit_off(service):
    """The same bug the enabled toggle had, in the control next to it.

    Skip is about tonight, and at eleven o'clock tonight has started. Dropping
    the plan takes the switch-off with it, and nothing else in the house performs
    that: the bed runs until the Shelly's own daily schedule catches it.
    """
    service.clock.jump_to(datetime(2026, 9, 13, 1, 0))
    service.skip_tonight(True)

    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert plan is not None, "a night already running is not cancelled, only wound up"
    # Named, not just non-None. Dropping tonight does not leave nothing behind: it
    # leaves *tomorrow*, which is a plan, has a wake time, and will happily answer
    # every question asked of it while the bed runs all day. A test that only
    # checked for None here would pass on the bug.
    assert plan.wake_at == datetime(2026, 9, 13, 7, 30), "tonight's, not tomorrow's"

    service.clock.jump_to(plan.wake_at)
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "power_off"


def test_skipping_before_it_starts_really_does_skip(service):
    """The other half. At eight in the evening nothing has begun, so skip means
    skip: tonight is not run, and the app looks past it to the next night."""
    service.skip_tonight(True)
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert plan is not None and plan.wake_at.date() == date(2026, 9, 14)


# --- 2. A nudge that nothing acts on --------------------------------------------


async def test_a_nudge_reaches_the_unit_when_it_is_asked_for(service):
    """"One degree cooler for half an hour" is a control you press because you are
    too warm now. Storing it and waiting for the next stage boundary, which may be
    three hours off, is not the feature."""
    plan = service.schedule.plan_for(TONIGHT)
    deep = next(s for s in plan.steps if s.stage is Stage.DEEP)
    service.clock.jump_to(deep.starts_at + timedelta(hours=1))
    await service._run_stage(plan, deep)
    assert service.state.assumed_target_c == 19

    await service.nudge_tonight(-1)
    assert service.state.assumed_target_c == 18, "the bed is a degree cooler now, not later"


async def test_a_nudge_puts_the_temperature_back_when_it_lapses(service):
    """It says "then back to the plan" on the screen. Something has to do that."""
    plan = service.schedule.plan_for(TONIGHT)
    deep = next(s for s in plan.steps if s.stage is Stage.DEEP)
    service.clock.jump_to(deep.starts_at + timedelta(hours=1))
    await service._run_stage(plan, deep)
    await service.nudge_tonight(-1, minutes=30)
    assert service.state.assumed_target_c == 18

    # Through the sampling beat, not by calling the method. Nothing outside this
    # file ever calls it, so a test that reaches for it directly would pass just
    # as happily on a version where it was never wired into the loop at all.
    service.clock.advance(timedelta(minutes=31))
    await service._sample_power()
    assert service.state.assumed_target_c == 19, "back to the plan, without waiting for REM"


# --- 6. Getting the bed ready for a night that is not the saved one -------------


async def test_getting_ready_aims_at_tonights_temperature(service):
    """Set Drift to 28 for tonight and the bed should be brought to 28.

    The mode was already being chosen from the plan built over tonight, so this
    was worse than either: warming picked for a 28C night, and 21C sent.
    """
    await service.set_stage_tonight(Stage.DRIFT, 28)
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert plan is not None and plan.precool_at is not None

    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.state.assumed_target_c == 28
    assert service._precondition is not None
    assert service._precondition.target_c == 28


# --- 7. A tonight that belongs to a night already over ---------------------------


def test_last_nights_skip_is_not_still_on_the_screen(service):
    """A row for a night that has passed is spent. The scheduler already ignores
    it; the app reads a different field and was still drawing it."""
    service.skip_tonight(True)
    assert service.tonight_state().skip is True

    # Tomorrow evening. Same service, never restarted, so nothing has re-read it.
    service.clock.jump_to(datetime(2026, 9, 13, 20, 0))
    assert service.tonight_state() is None or service.tonight_state().skip is False


# --- 4. A correction that eats itself -------------------------------------------


def test_a_working_correction_does_not_argue_itself_back_to_nothing():
    """The subtle one, and the one that would have taken weeks to notice.

    The bed lands 2.1C low, so the app starts sending 30 to get 28. It works: the
    bed now lands on 28. Recorded against the 28 that was asked for, that reads as
    no droop at all, which averages the correction away, which puts the bed back
    to 25.9, which earns the correction again. A slow oscillation with nothing in
    the log to say why.

    The fix is to record what was actually transmitted, so the measurement stays
    "this bed lands 2.1 below whatever is sent".
    """
    db = Database(":memory:")
    try:
        for _ in range(MIN_RUNS_TO_LEARN):
            db.record_precondition(
                NOW, "warming", 28, 3300, True,
                start_c=19.5, end_c=25.9, sent_c=28, decided_by="probes",
            )
        assert db.learned_offset_c("warming", 28) == -2.1

        # Now the correction is in force: 30 goes out and the bed lands on 28.
        # Set the first three aside so this is the steady state on its own rather
        # than an average of before and after.
        db.forget_learning()
        for _ in range(MIN_RUNS_TO_LEARN):
            db.record_precondition(
                NOW, "warming", 28, 3300, True,
                start_c=19.5, end_c=28.0, sent_c=30, decided_by="probes",
            )
        assert db.learned_offset_c("warming", 28) == -2.0, "still about two degrees of droop"

        # And the counterfactual, which is the whole point: scored against the 28
        # that was asked for, those same three nights read as a bed that lands
        # dead on, and the correction that put it there would be averaged away.
        rows = db._db.execute(
            "SELECT end_c - target_c AS naive FROM precondition_runs WHERE counts = 1"
        ).fetchall()
        assert all(r["naive"] == 0.0 for r in rows)
    finally:
        db.close()


# --- 5. Drift arriving without moving anything else -----------------------------


def test_drift_comes_out_of_deep_rather_than_out_of_the_whole_night():
    """Seeding Drift from Deep keeps the temperature curve identical, which is
    what the migration promised. Rescaling the whole night still moved REM by
    seventeen minutes and Wake by two, which it did not promise and nobody asked
    for."""
    before = [
        SleepStage(Stage.DEEP, 240, 19),
        SleepStage(Stage.REM, 210, 22),
        SleepStage(Stage.WAKE, 30, 26),
    ]
    after = Schedule(
        bed_time=time(22, 30), wake_time=time(6, 30),
        stages=[SleepStage(s.stage, s.duration_minutes, s.temp_c) for s in before],
    ).stages

    got = {s.stage: s.duration_minutes for s in after}
    assert got[Stage.DRIFT] == DRIFT_MINUTES
    assert got[Stage.DEEP] == 240 - DRIFT_MINUTES, "taken from the stage it was seeded from"
    assert got[Stage.REM] == 210, "and from nowhere else"
    assert got[Stage.WAKE] == 30


# --- The second round, on de3aba5 -------------------------------------------------
#
# Three of these four are regressions in the fixes above, which is its own lesson:
# a fix written against one failing test passes that test and is not thereby
# correct. The skip fix in particular traded one lost shutdown for two others.


def test_a_lie_in_survives_a_restart(service):
    """The Pi restarts routinely and the deadline has to come back with it.

    `night_date` works out which night we are in from the **saved** schedule, on
    purpose: you need the date to look tonight up, so the date cannot depend on
    what tonight says. But the saved alarm is not tonight's alarm during a lie-in,
    so past the saved one the lookup moved on to the next night, found nothing
    stored for it, and tonight's shift went with it. Four hours is what the app
    lets somebody ask for.
    """
    service.shift_tonight(wake_minutes=240)
    assert service.tonight_now().wake_time == time(11, 30)

    # Ten in the morning: past the usual 07:30 alarm and its grace, still an hour
    # and a half before the one that was actually asked for.
    service.clock.jump_to(datetime(2026, 9, 13, 10, 0))
    again = Service(
        Settings(db_path=service.settings.db_path), clock=VirtualClock(service.clock.now()),
        echo=False,
    )
    try:
        again.schedule = service.schedule
        again.load_tonight()
        assert again.tonight_now().wake_time == time(11, 30), "the lie-in is still on"
        plan = again.scheduler.plan_in_progress(again.schedule, again.clock.now())
        assert plan is not None
        assert plan.wake_at == datetime(2026, 9, 13, 11, 30), "and so is its switch-off"
    finally:
        again.db.close()


def test_skipping_keeps_the_deadline_that_was_actually_set(service):
    """Skip says do not run tonight. It does not say forget that the alarm moved:
    the unit is on, and the time it has to be off by is the one that was asked
    for, not the one on the saved routine."""
    service.clock.jump_to(datetime(2026, 9, 13, 1, 0))
    service.shift_tonight(wake_minutes=120)
    service.skip_tonight(True)

    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert plan is not None
    assert plan.wake_at == datetime(2026, 9, 13, 9, 30), "tonight's deadline, not the usual one"


def test_last_nights_skip_does_not_cancel_tonight(service):
    """only_finishing was taught about skip and not about the calendar, so a Pi
    holding a spent row cancelled every stage of a night that was going to run.
    Worse than the bug it was added to fix: that one lost a switch-off, this one
    loses the whole night and leaves the bed wherever it was."""
    service.skip_tonight(True)

    # Five minutes past the next night's bedtime, so a stage really is due rather
    # than mostly over. Thirty minutes into a thirty five minute Drift is past
    # STAGE_GRACE, and a test that read None there would be reading the wrong rule.
    service.clock.jump_to(datetime(2026, 9, 13, 22, 35))
    assert service.scheduler.only_finishing(service.schedule, service.clock.now()) is False
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "stage", "tomorrow night runs normally"
    assert job.step is not None and job.step.stage is Stage.DRIFT


async def test_cancelling_a_nudge_puts_the_bed_back_at_once(service):
    """The cross on the nudge row is an undo, and an undo that leaves the bed a
    degree out for another half hour is not one. The cooldown that stops a dead
    blaster being hammered was being applied to the very next thing somebody
    asked for."""
    plan = service.schedule.plan_for(TONIGHT)
    deep = next(s for s in plan.steps if s.stage is Stage.DEEP)
    service.clock.jump_to(deep.starts_at + timedelta(hours=1))
    await service._run_stage(plan, deep)

    await service.nudge_tonight(-1)
    assert service.state.assumed_target_c == 18

    await service.nudge_tonight(0)
    assert service.state.assumed_target_c == 19, "back now, not in half an hour"


async def test_editing_a_running_stage_into_the_other_mode_works(service):
    """Deep is cooling at 19. Ask for 40 and the unit has to warm: cooling tops
    out at 35 and cannot express it at all. The running mode was being kept
    whatever was asked for, so a legal request failed and said so only in a log
    nobody was reading."""
    plan = service.schedule.plan_for(TONIGHT)
    deep = next(s for s in plan.steps if s.stage is Stage.DEEP)
    service.clock.jump_to(deep.starts_at + timedelta(hours=1))
    await service._run_stage(plan, deep)
    assert service.state.assumed_mode is not Mode.WARMING

    await service.set_stage_tonight(Stage.DEEP, 40)
    assert service.state.assumed_mode is Mode.WARMING
    assert service.state.assumed_target_c == 40
