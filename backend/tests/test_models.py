"""The derivations everything else is built on.

If the arm time is wrong by a day, or the rail count is wrong by a press, nothing
downstream can save it. These are cheap to check and worth checking.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze.models import (
    Activity,
    Mode,
    Power,
    PowerThresholds,
    Schedule,
    Tristate,
    DeviceState,
    SCHEDULE_DURATION,
    plan_for_wake,
    rail_count,
    range_for,
)


def test_schedule_runs_eight_and_a_half_hours():
    assert SCHEDULE_DURATION == timedelta(hours=8, minutes=30)


def test_wake_time_works_backwards_to_the_evening_before():
    # The headline example from the brief: wake 06:30 means arm at 22:00 the
    # night before, and pre-cool 30 minutes before that.
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), precool_lead_minutes=30)
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)
    assert plan.arm_at == datetime(2026, 9, 7, 22, 0)
    assert plan.precool_at == datetime(2026, 9, 7, 21, 30)


def test_pre_cool_can_be_turned_off():
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), precool_enabled=False)
    assert plan.precool_at is None
    assert plan.starts_at == plan.arm_at


def test_a_late_wake_time_keeps_arming_on_the_same_day():
    # Wake at 11:00 arms at 02:30 the same morning, not the evening before.
    plan = plan_for_wake(date(2026, 9, 8), time(11, 0), precool_enabled=False)
    assert plan.arm_at == datetime(2026, 9, 8, 2, 30)


@pytest.mark.parametrize(
    ("mode", "expected_range", "expected_rail"),
    [
        (Mode.QUIET, (15, 35), 25),
        (Mode.STANDARD, (15, 35), 25),
        (Mode.TURBO, (15, 35), 25),
        (Mode.WARMING, (25, 55), 35),
    ],
)
def test_ranges_and_rail_counts_match_the_manual(mode, expected_range, expected_rail):
    # 25 presses in cooling and 35 in warming: the span plus five, which is what
    # makes a temperature set idempotent from any starting point and what absorbs
    # the two discarded presses of the wake preamble.
    assert range_for(mode) == expected_range
    assert rail_count(mode) == expected_rail


def test_days_of_week_are_keyed_to_the_wake_morning():
    # Monday to Friday means five wake mornings, so the first arming of the week
    # happens on Sunday evening.
    schedule = Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])
    plan = schedule.next_plan(datetime(2026, 9, 6, 12, 0))  # a Sunday lunchtime
    assert plan is not None
    assert plan.wake_at == datetime(2026, 9, 7, 6, 30)  # Monday morning
    assert plan.arm_at == datetime(2026, 9, 6, 22, 0)  # Sunday evening


def test_next_plan_skips_a_night_already_under_way():
    schedule = Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])
    # Sunday 23:00: Monday's arming at 22:00 has been and gone.
    plan = schedule.next_plan(datetime(2026, 9, 6, 23, 0))
    assert plan is not None
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)  # Tuesday


def test_a_disabled_schedule_has_no_next_plan():
    assert Schedule(enabled=False).next_plan(datetime(2026, 9, 6, 12, 0)) is None
    assert Schedule(days_of_week=[]).next_plan(datetime(2026, 9, 6, 12, 0)) is None


def test_needs_write_is_true_until_it_has_been_sent_to_the_unit():
    fresh = Schedule()
    assert fresh.needs_write  # never written

    written = Schedule(
        last_written_at=datetime(2026, 9, 6, 20, 0),
        updated_at=datetime(2026, 9, 6, 19, 0),
    )
    assert not written.needs_write

    edited = written.with_updates(datetime(2026, 9, 6, 21, 0), phase1_temp_c=18)
    assert edited.needs_write


def test_temperature_is_only_adjustable_when_on_and_out_of_a_schedule():
    # Both of the unit's real constraints: when it is off only power responds, and
    # while a schedule runs the temperature buttons do nothing at all.
    assert DeviceState(power=Power.ON, in_schedule=Tristate.FALSE).can_set_temperature
    assert not DeviceState(power=Power.OFF, in_schedule=Tristate.FALSE).can_set_temperature
    assert not DeviceState(power=Power.ON, in_schedule=Tristate.TRUE).can_set_temperature
    assert not DeviceState(power=Power.ON, in_schedule=Tristate.UNKNOWN).can_set_temperature


@pytest.mark.parametrize(
    ("watts", "expected"),
    [
        (None, Activity.UNKNOWN),
        (0.4, Activity.OFF),
        (32.0, Activity.IDLE),
        (170.0, Activity.COOLING),
        (300.0, Activity.HEATING),
    ],
)
def test_power_thresholds_classify_the_plug_reading(watts, expected):
    assert PowerThresholds().classify(watts) == expected
