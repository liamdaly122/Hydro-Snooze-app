"""The derivations everything else is built on.

If the arm time is wrong by a day, or the rail count is wrong by a press, nothing
downstream can save it. These are cheap to check and worth checking.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze.models import (
    Activity,
    DeviceState,
    Mode,
    Power,
    PowerThresholds,
    Schedule,
    SleepStage,
    Stage,
    default_stages,
    mode_for_target,
    modes_for,
    plan_for_wake,
    rail_count,
    range_for,
)


def test_the_night_is_as_long_as_its_stages():
    # No fixed 8h30m any more. The night is however long you make it, which is
    # the whole reason for dropping the unit's own scheduler.
    stages = [SleepStage(Stage.DEEP, 120, 17), SleepStage(Stage.WAKE, 60, 26)]
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), stages, precool_lead_minutes=30)
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)
    assert plan.bedtime_at == datetime(2026, 9, 8, 3, 30)
    assert plan.precool_at == datetime(2026, 9, 8, 3, 0)


def test_stages_run_in_order_and_finish_at_the_wake_time():
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), default_stages())
    assert [s.stage for s in plan.steps] == [Stage.DEEP, Stage.REM, Stage.WAKE]
    assert plan.steps[0].starts_at == plan.bedtime_at
    assert plan.steps[-1].ends_at == plan.wake_at
    # Each one picks up where the last left off, with no gaps.
    for earlier, later in zip(plan.steps, plan.steps[1:]):
        assert earlier.ends_at == later.starts_at


def test_a_night_can_cool_then_heat():
    # The thing the unit's own scheduler made impossible: it refuses to switch
    # between cooling and warming once armed.
    plan = plan_for_wake(
        date(2026, 9, 8),
        time(6, 30),
        [SleepStage(Stage.DEEP, 240, 17), SleepStage(Stage.WAKE, 60, 28)],
    )
    assert plan.steps[0].mode is Mode.QUIET
    assert plan.steps[1].mode is Mode.WARMING


def test_pre_conditioning_can_be_turned_off():
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), default_stages(), precool_enabled=False)
    assert plan.precool_at is None
    assert plan.starts_at == plan.bedtime_at


@pytest.mark.parametrize(
    ("temp", "expected"),
    [(15, Mode.QUIET), (24, Mode.QUIET), (25, Mode.WARMING), (30, Mode.WARMING)],
)
def test_a_temperature_on_its_own_cools_below_25_and_warms_at_or_above(temp, expected):
    # Below 25 it has to cool, because warming cannot express a number that low.
    # With nothing to come from there is no direction of travel to go on, so the
    # rest is the old rule: 25 and above warms.
    assert mode_for_target(temp, Mode.QUIET) is expected


def test_the_cooling_speed_carries_through_to_cooling_stages_only():
    assert mode_for_target(18, Mode.TURBO) is Mode.TURBO
    assert mode_for_target(27, Mode.TURBO) is Mode.WARMING


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
    # Monday to Friday means five wake mornings, so the first night of the week
    # starts on Sunday evening.
    schedule = Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])
    plan = schedule.plan_for(date(2026, 9, 7))  # Monday morning
    assert plan.wake_at == datetime(2026, 9, 7, 6, 30)
    assert plan.bedtime_at.date() == date(2026, 9, 6)  # Sunday evening


def test_temperature_is_adjustable_whenever_the_unit_is_on():
    # It used to also be dead during the unit's own sleep schedule. The app never
    # arms that now, so the only constraint left is that the unit is on.
    assert DeviceState(power=Power.ON).can_set_temperature
    assert not DeviceState(power=Power.OFF).can_set_temperature
    assert not DeviceState(power=Power.UNKNOWN).can_set_temperature


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


# --- The overlap ---------------------------------------------------------------
#
# Cooling reaches 15 to 35 and warming reaches 25 to 55, so between 25 and 35 both
# modes can be set to the number. Only one of them can move the bed there, and
# which one depends on where the bed is coming from.


@pytest.mark.parametrize("target", [25, 30, 35])
def test_coming_down_into_the_overlap_cools(target):
    """The bug this fixes: 30C to 25C in warming mode sets the right number and
    then sits idle while the bed coasts down on its own."""
    assert mode_for_target(target, Mode.QUIET, coming_from_c=target + 5) is Mode.QUIET


@pytest.mark.parametrize("target", [25, 30, 35])
def test_going_up_into_the_overlap_warms(target):
    assert mode_for_target(target, Mode.QUIET, coming_from_c=target - 5) is Mode.WARMING


def test_the_cooling_speed_is_used_for_a_descent_into_the_overlap():
    assert mode_for_target(30, Mode.TURBO, coming_from_c=34) is Mode.TURBO


@pytest.mark.parametrize(
    ("target", "coming_from", "expected"),
    [
        # Below warming's floor, cooling is the only mode that expresses it.
        (24, 30, Mode.QUIET),
        (24, 20, Mode.QUIET),
        # Above cooling's ceiling, warming is the only one that expresses it.
        (40, 55, Mode.WARMING),
        (40, 30, Mode.WARMING),
    ],
)
def test_outside_the_overlap_the_direction_cannot_change_anything(target, coming_from, expected):
    assert mode_for_target(target, Mode.QUIET, coming_from_c=coming_from) is expected


def test_standing_still_in_the_overlap_keeps_the_mode_it_was_in():
    """Two stages at the same temperature need no mode change, and switching to
    warming mid-descent would stop the bed before it arrived."""
    assert (
        mode_for_target(30, Mode.QUIET, coming_from_c=30, coming_from_mode=Mode.QUIET)
        is Mode.QUIET
    )
    assert (
        mode_for_target(30, Mode.QUIET, coming_from_c=30, coming_from_mode=Mode.WARMING)
        is Mode.WARMING
    )


def _night(*temps: int) -> list[SleepStage]:
    return [
        SleepStage(stage, 60, temp)
        for stage, temp in zip((Stage.DEEP, Stage.REM, Stage.WAKE), temps, strict=True)
    ]


@pytest.mark.parametrize(
    ("temps", "expected"),
    [
        # The night Liam asked about: warm to 30, then genuinely cool to 25.
        ((30, 25, 26), [Mode.WARMING, Mode.QUIET, Mode.WARMING]),
        # Nothing changes for a night that never enters the overlap.
        ((17, 20, 24), [Mode.QUIET, Mode.QUIET, Mode.QUIET]),
        # Or for the default night, which only climbs.
        ((17, 20, 26), [Mode.QUIET, Mode.QUIET, Mode.WARMING]),
        # A descent out of warming-only territory picks cooling as soon as it can.
        ((40, 30, 24), [Mode.WARMING, Mode.QUIET, Mode.QUIET]),
        # A hold after a descent stays cooling rather than flipping back.
        ((32, 30, 30), [Mode.WARMING, Mode.QUIET, Mode.QUIET]),
        # And a hold after a climb stays warming.
        ((24, 30, 30), [Mode.QUIET, Mode.WARMING, Mode.WARMING]),
    ],
)
def test_a_night_resolves_its_modes_in_order(temps, expected):
    assert modes_for(_night(*temps), Mode.QUIET) == expected


def test_the_plan_carries_the_resolved_modes_not_the_per_stage_guess():
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), _night(30, 25, 26))
    assert [step.mode for step in plan.steps] == [Mode.WARMING, Mode.QUIET, Mode.WARMING]
