"""When things fire, and just as importantly when they do not."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from hydrosnooze.models import Mode, Schedule
from hydrosnooze.scheduler import Scheduler


@pytest.fixture
def schedule() -> Schedule:
    # Wake 06:30 on weekday mornings, so arming happens the evening before.
    return Schedule(
        wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4], mode=Mode.QUIET, precool_lead_minutes=30
    )


def test_nothing_is_due_in_the_afternoon(schedule):
    assert Scheduler().due(schedule, datetime(2026, 9, 7, 15, 0)) is None


def test_precool_fires_at_half_past_nine(schedule):
    job, plan = Scheduler().due(schedule, datetime(2026, 9, 7, 21, 30))
    assert job == "precool"
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)


def test_arming_fires_at_ten(schedule):
    s = Scheduler()
    s.fired.mark("precool", s.plan_in_progress(schedule, datetime(2026, 9, 7, 21, 30)))
    job, _ = s.due(schedule, datetime(2026, 9, 7, 22, 0))
    assert job == "arm"


def test_a_job_only_fires_once(schedule):
    s = Scheduler()
    job, plan = s.due(schedule, datetime(2026, 9, 7, 21, 35))
    s.fired.mark(job, plan)
    assert s.due(schedule, datetime(2026, 9, 7, 21, 40)) is None


def test_a_late_start_still_pre_cools(schedule):
    # Booting at quarter to ten should still pre-cool, just with less lead time.
    job, _ = Scheduler().due(schedule, datetime(2026, 9, 7, 21, 45))
    assert job == "precool"


def test_arming_is_not_attempted_hours_late(schedule):
    # Arming at 2am would run the schedule until half past ten in the morning.
    # Better to leave it alone and say so.
    s = Scheduler()
    assert s.due(schedule, datetime(2026, 9, 8, 2, 0)) is None
    assert s.missed_arming(schedule, datetime(2026, 9, 8, 2, 0)) is not None


def test_the_wake_check_fires_in_the_morning(schedule):
    s = Scheduler()
    plan = s.plan_in_progress(schedule, datetime(2026, 9, 7, 22, 0))
    s.fired.mark("precool", plan)
    s.fired.mark("arm", plan)
    job, _ = s.due(schedule, datetime(2026, 9, 8, 6, 30))
    assert job == "wake_check"


def test_saturday_night_is_skipped_when_sunday_is_not_selected(schedule):
    # Weekdays means five wake mornings, so nothing arms on a Saturday evening.
    assert Scheduler().due(schedule, datetime(2026, 9, 12, 22, 0)) is None


def test_sunday_evening_arms_for_monday_morning(schedule):
    job, plan = Scheduler().due(schedule, datetime(2026, 9, 6, 21, 30))
    assert job == "precool"
    assert plan.wake_at == datetime(2026, 9, 7, 6, 30)
    assert plan.arm_at == datetime(2026, 9, 6, 22, 0)


def test_a_disabled_schedule_never_fires(schedule):
    schedule.enabled = False
    assert Scheduler().due(schedule, datetime(2026, 9, 7, 21, 30)) is None
