"""What a restart costs, now that restarts are routine.

`Restart=always` catches a crash and the watchdog catches a stall, which is the
point of both. It means the service comes back mid-night as a matter of course
rather than as an emergency, and everything it knew has to survive that.

The marks saying which jobs have already run lived only in memory until now. A
restart at 3am wiped them, the next tick decided every stage that had already run
had been missed, and the notifier pushed that to a phone. A false alarm at 3am is
worse than no alarm: it is how you learn to ignore the real one.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.models import Schedule
from hydrosnooze.notify import Notifier
from hydrosnooze.scheduler import Job, Scheduler
from hydrosnooze.service import Service

THREE_AM = datetime(2026, 9, 8, 3, 0)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "hydrosnooze.db")


def scheduler_on(db_path: str) -> Scheduler:
    """A scheduler as the service builds one: marks read back from storage."""
    db = Database(db_path)
    sched = Scheduler()
    sched.fired.done = db.fired_marks()
    sched.fired.store = db.set_fired_marks
    return sched


def a_night(sched: Scheduler, schedule: Schedule):
    return sched.plan_in_progress(schedule, datetime(2026, 9, 7, 23, 0))


def test_a_perfect_night_reports_nothing_missed_after_a_restart(db_path):
    """The failure this is all for. Every job ran, then systemd restarted the
    service, and the first tick afterwards must not invent two failures."""
    schedule = Schedule()

    before = scheduler_on(db_path)
    night = a_night(before, schedule)
    before.fired.mark(Job("precool", night))
    for step in night.steps:
        before.fired.mark(Job("stage", night, step))
    assert before.missed(schedule, THREE_AM) == []

    after = scheduler_on(db_path)
    assert [job.key for job in after.missed(schedule, THREE_AM)] == []


def test_the_marks_come_back_rather_than_starting_empty(db_path):
    schedule = Schedule()
    before = scheduler_on(db_path)
    night = a_night(before, schedule)
    deep = Job("stage", night, night.steps[0])
    before.fired.mark(deep)

    after = scheduler_on(db_path)
    assert after.fired.has_fired(deep)


def test_a_stage_that_really_was_missed_is_still_reported(db_path):
    """The other half of it. Persisting the marks would be no use if it also
    silenced the alarm that matters."""
    schedule = Schedule()
    sched = scheduler_on(db_path)
    night = a_night(sched, schedule)
    # Only the first stage ran.
    sched.fired.mark(Job("stage", night, night.steps[0]))

    after = scheduler_on(db_path)
    missed = [job.key for job in after.missed(schedule, THREE_AM)]
    assert missed == ["stage:rem"]


def test_last_nights_marks_do_not_count_for_tonight(db_path):
    """Keyed by wake time, so a row left over from yesterday does not match
    tonight's plan. Nothing prunes this table and nothing needs to."""
    schedule = Schedule()
    sched = scheduler_on(db_path)
    yesterday = sched.plan_in_progress(schedule, datetime(2026, 9, 6, 23, 0))
    tonight = a_night(sched, schedule)
    assert yesterday.wake_at != tonight.wake_at

    sched.fired.mark(Job("stage", yesterday, yesterday.steps[0]))

    after = scheduler_on(db_path)
    assert not after.fired.has_fired(Job("stage", tonight, tonight.steps[0]))


def test_clearing_clears_the_stored_copy_too(db_path):
    """A rehearsal clears the marks. If that only cleared memory, the next
    restart would bring them all back and skip the night."""
    schedule = Schedule()
    sched = scheduler_on(db_path)
    night = a_night(sched, schedule)
    sched.fired.mark(Job("stage", night, night.steps[0]))
    sched.fired.clear()

    assert scheduler_on(db_path).fired.done == {}


def test_the_table_never_grows(db_path):
    """Five keys, one row each. Worth pinning: this database lives on an SD card
    and an unbounded table would be a slow way to kill one."""
    schedule = Schedule()
    sched = scheduler_on(db_path)
    for day in (5, 6, 7):
        plan = sched.plan_in_progress(schedule, datetime(2026, 9, day, 23, 0))
        sched.fired.mark(Job("precool", plan))
        for step in plan.steps:
            sched.fired.mark(Job("stage", plan, step))
        sched.fired.mark(Job("power_off", plan))

    assert len(Database(db_path).fired_marks()) == len(sched.fired.done) <= 5


def test_a_storage_failure_does_not_stop_the_night(db_path, caplog):
    """The mark is written after the job has already run. Raising here would
    abandon a tick that was reporting a success."""
    schedule = Schedule()
    sched = scheduler_on(db_path)

    def broken(_marks):
        raise OSError("read-only file system")

    sched.fired.store = broken
    night = a_night(sched, schedule)
    deep = Job("stage", night, night.steps[0])
    sched.fired.mark(deep)

    assert sched.fired.has_fired(deep)
    assert "could not save the fired marks" in caplog.text


def test_the_false_alarms_would_have_reached_the_phone(db_path):
    """Why this was worth fixing rather than filing. Both invented failures are
    at error level, and everything at error level is pushed to the phone."""
    from hydrosnooze.clock import VirtualClock
    from hydrosnooze.events import EventLog

    schedule = Schedule()
    ran = Scheduler()
    night = a_night(ran, schedule)
    for step in night.steps:
        ran.fired.mark(Job("stage", night, step))
    assert ran.missed(schedule, THREE_AM) == []

    # The same night, seen by a service that has just been restarted and has no
    # store to read back from. This is what shipped before today.
    forgetful = Scheduler()
    missed = forgetful.missed(schedule, THREE_AM)
    assert len(missed) == 2

    # Worded as the service words them, and put through the same filter.
    events = EventLog(VirtualClock(THREE_AM))
    for job in missed:
        assert job.step is not None
        events.error("stage", f"Missed the {job.step.stage.value} stage at 02:30.")
    notifier = Notifier(VirtualClock(THREE_AM), topic="t")
    assert [e for e in events.recent(10) if notifier.worth_sending(e)] != []


@pytest.mark.asyncio
async def test_the_service_reads_the_marks_back_on_startup(db_path):
    """End to end, through the object systemd actually restarts."""
    settings = Settings(db_path=db_path)

    before = Service(settings, echo=False)
    night = a_night(before.scheduler, before.schedule)
    before.scheduler.fired.mark(Job("precool", night))
    for step in night.steps:
        before.scheduler.fired.mark(Job("stage", night, step))

    after = Service(settings, echo=False)
    assert after.scheduler.missed(after.schedule, THREE_AM) == []
