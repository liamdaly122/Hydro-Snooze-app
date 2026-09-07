"""The derivations everything else is built on.

If the arm time is wrong by a day, or the rail count is wrong by a press, nothing
downstream can save it. These are cheap to check and worth checking.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze.models import (
    MIN_STAGE_MINUTES,
    MINUTES_IN_A_DAY,
    Activity,
    DeviceState,
    Mode,
    Power,
    PowerThresholds,
    Schedule,
    SleepStage,
    Stage,
    default_stages,
    fit_stages,
    minutes_between,
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
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), stages)
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)
    assert plan.bedtime_at == datetime(2026, 9, 8, 3, 30)
    # 17C from a 20C room is three degrees of Turbo, so a short head start.
    assert plan.precool_at == plan.bedtime_at - timedelta(minutes=plan.preconditioning.lead_minutes)


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


def test_pre_conditioning_is_skipped_when_the_bed_is_already_there():
    """There is no off switch. There does not need to be: a first stage at room
    temperature has nothing to close, so nothing runs."""
    stages = [SleepStage(Stage.DEEP, 240, 20), SleepStage(Stage.WAKE, 30, 26)]
    plan = plan_for_wake(date(2026, 9, 8), time(6, 30), stages)
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


# --- Bedtime, and the stages that have to fit inside it -----------------------
#
# Bedtime used to fall out of the durations, which meant three fifteen minute
# stages produced a forty five minute night. It is set now, with the wake time,
# and the two of them decide how long the night is.


def test_the_night_is_the_gap_between_the_two_times():
    assert minutes_between(time(22, 30), time(6, 30)) == 480
    assert minutes_between(time(23, 0), time(7, 0)) == 480
    # Both sides of midnight, and both on the same side of it.
    assert minutes_between(time(1, 0), time(6, 30)) == 330
    assert minutes_between(time(6, 30), time(22, 30)) == 960


def test_the_same_time_twice_is_a_whole_day_not_nothing():
    assert minutes_between(time(6, 30), time(6, 30)) == MINUTES_IN_A_DAY


def test_the_stages_always_add_up_to_the_night():
    schedule = Schedule(bed_time=time(23, 0), wake_time=time(6, 30))
    assert schedule.total_minutes == schedule.night_minutes == 450


def test_moving_bedtime_takes_the_time_off_in_proportion():
    """An hour later to bed is an hour off the night, shared out in the shape the
    night already had, not taken off whichever stage happens to be last."""
    before = Schedule(bed_time=time(22, 30), wake_time=time(6, 30))
    after = replace(before, bed_time=time(23, 30))

    assert [s.duration_minutes for s in before.stages] == [240, 210, 30]
    assert sum(s.duration_minutes for s in after.stages) == 420
    assert after.stages[0].duration_minutes > after.stages[1].duration_minutes
    assert all(s.duration_minutes >= MIN_STAGE_MINUTES for s in after.stages)


def test_the_parts_add_up_to_the_whole_exactly():
    """Rounding each stage on its own loses a minute or gains one. Over a night
    that is invisible, and it still means bedtime is not when it says it is."""
    for minutes in range(45, 24 * 60, 7):
        stages = fit_stages(default_stages(), minutes)
        assert sum(s.duration_minutes for s in stages) == minutes, minutes


def test_no_stage_is_ever_squeezed_out_of_existence():
    stages = fit_stages(default_stages(), MIN_STAGE_MINUTES * 3)
    assert [s.duration_minutes for s in stages] == [15, 15, 15]


def test_a_short_night_keeps_its_temperatures():
    stages = fit_stages(default_stages(), 60)
    assert [s.temp_c for s in stages] == [17, 20, 26]
    assert [s.stage for s in stages] == [Stage.DEEP, Stage.REM, Stage.WAKE]


def test_bedtime_in_the_plan_is_the_bedtime_that_was_set():
    schedule = Schedule(bed_time=time(23, 15), wake_time=time(6, 30))
    plan = schedule.plan_for(date(2026, 9, 8))
    assert plan.bedtime_at == datetime(2026, 9, 7, 23, 15)
    assert plan.wake_at == datetime(2026, 9, 8, 6, 30)
    assert plan.steps[-1].ends_at == plan.wake_at


# --- The four states, as the plug actually reads them --------------------------
#
# Real readings from a King HS1001 through a Shelly Plug S Gen3, walked through
# every state with the physical remote. These are a regression test, not a
# derivation: if someone retunes the thresholds, these say what breaks.


@pytest.mark.parametrize(
    ("watts", "expected"),
    [
        # Off at the wall. Standby only.
        (1.2, Activity.OFF),
        (1.4, Activity.OFF),
        (1.6, Activity.OFF),
        # On and idling. Warming idles at 5, cooling idles at 9.
        (4.9, Activity.IDLE),
        (5.0, Activity.IDLE),
        (9.2, Activity.IDLE),
        (10.2, Activity.IDLE),
        # Actively cooling.
        (161.1, Activity.COOLING),
        (166.9, Activity.COOLING),
        (188.3, Activity.COOLING),
        # Actively heating.
        (303.7, Activity.HEATING),
        (310.4, Activity.HEATING),
        (393.4, Activity.HEATING),
    ],
)
def test_the_measured_states_classify_correctly(watts, expected):
    assert PowerThresholds().classify(watts) is expected


def test_idling_in_warming_is_not_mistaken_for_off():
    """The one that mattered. Warming idles at 4.9 W, and the old 5 W threshold
    read that as off, so the app pressed power at a stage boundary to turn on a
    unit that was already on. Which turned it off. In the middle of the night."""
    assert PowerThresholds().classify(4.9) is Activity.IDLE
    assert PowerThresholds().off_max_w < 4.9


def test_every_threshold_sits_clear_of_both_states_it_separates():
    t = PowerThresholds()
    assert 1.6 < t.off_max_w < 4.9, "between standby and idle"
    assert 10.2 < t.idle_max_w < 161.1, "between idle and cooling"
    assert 188.3 < t.cooling_max_w < 303.7, "between cooling and heating"
