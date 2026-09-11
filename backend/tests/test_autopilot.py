"""Autopilot: last night as numbers, and the three ways counting it went wrong.

Every bug in this file's history was a counting bug, and none of them would have
shown up as an error. They show up as a number on a screen that is quietly two
out, which is the worst way for a number to be wrong: nothing breaks, and you
believe it.

    counting boundaries      undercounts by half. One boundary costs a mode press
                             and thirty five presses of rail-and-count
    counting the outcome     the "bed reached 19C" line is the same action as the
                             "pre-cooling to 19C" line half an hour earlier
    looking only backwards   a drift correction presses and *then* explains
                             itself, so every one of them read as set by hand
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze import autopilot
from hydrosnooze.autopilot import BY_HAND, PHASE_KIND, READY_KIND, RESPONSE_KIND
from hydrosnooze.db import PreconditionRow, Sample
from hydrosnooze.events import Event
from hydrosnooze.models import Schedule, SleepStage, Stage

WAKE = date(2026, 9, 12)


@pytest.fixture
def plan():
    return Schedule(
        wake_time=time(7, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    ).plan_for(WAKE)


def ev(at: datetime, kind: str, message: str = "x", level: str = "info") -> Event:
    return Event(id=0, at=at, level=level, kind=kind, message=message)


def samples(plan, *, offset: float = 0.0, every=timedelta(minutes=2)) -> list[Sample]:
    """A bed sitting a fixed distance from whatever stage is running."""
    out: list[Sample] = []
    at = plan.steps[0].starts_at
    while at < plan.wake_at:
        step = next((s for s in plan.steps if s.starts_at <= at < s.ends_at), None)
        if step is not None:
            out.append(Sample(at=at, watts=170.0, return_c=step.temp_c + offset))
        at += every
    return out


def build(plan, events, rows=None, ready=None):
    return autopilot.build(plan, rows or [], events, set(), ready)


# --- What an adjustment is ------------------------------------------------------


def test_an_adjustment_is_a_command_not_a_boundary(plan):
    """One boundary costs a mode press and then rail-and-count. Counting the
    boundary itself says three where the unit was told six things."""
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND, "Deep: 19C in quiet"),
        ev(at, "mode", "Set mode to quiet"),
        ev(at + timedelta(seconds=40), "temperature", "Railed to 15C then up to 19C"),
    ])
    assert night.adjustments == 2, "the boundary is the reason, not the adjustment"


def test_the_boundary_is_what_the_adjustment_is_filed_under(plan):
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND),
        ev(at, "mode"),
        ev(at, "temperature"),
    ])
    assert night.counted(PHASE_KIND) == 2
    assert {m.label for m in night.marks} == {"Phase & mode change"}


def test_the_result_of_getting_ready_is_not_a_second_adjustment(plan):
    """"Pre-cooling to 19C" and "the bed reached 19C" half an hour later are one
    action reported twice. Counting both made every night read one too high."""
    start = plan.precool_at or plan.bedtime_at
    night = build(plan, [
        ev(start, autopilot.AMBIENT_KIND, "Pre-cooling in turbo to 19C"),
        ev(start, "mode"),
        ev(start + timedelta(minutes=29), READY_KIND, "The bed reached 19C in 29m"),
    ])
    # len(marks), not just the headline. Counting the result would have filed it
    # under "set by hand" (its reason is half an hour behind it), which the
    # headline excludes, so asserting on the headline alone passed either way.
    assert len(night.marks) == 1, "the outcome was counted as an action"
    assert night.adjustments == 1
    assert night.counted(READY_KIND) == 0


# --- Why it happened ------------------------------------------------------------


def test_a_reason_explains_the_commands_after_it(plan):
    at = plan.steps[1].starts_at
    night = build(plan, [ev(at, PHASE_KIND), ev(at + timedelta(seconds=30), "temperature")])
    assert night.marks[0].kind == PHASE_KIND


def test_a_reason_explains_the_commands_before_it_too(plan):
    """The one that had every drift correction of every night filed under "set by
    hand". A boundary announces itself and then presses; a drift correction
    presses and then explains itself, because the sentence describes what it just
    did."""
    at = plan.steps[2].starts_at + timedelta(minutes=30)
    night = build(plan, [
        ev(at, "mode", "Set mode to quiet"),
        ev(at, "temperature", "Railed to 15C then up to 26C"),
        ev(at + timedelta(seconds=5), RESPONSE_KIND, "The bed is at 25.5C against a 26C stage"),
    ])
    assert [m.kind for m in night.marks] == [RESPONSE_KIND, RESPONSE_KIND]


def test_a_command_with_no_reason_near_it_was_somebody_pressing_a_button(plan):
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND),
        ev(at, "temperature"),
        ev(at + timedelta(hours=2), "temperature", "Railed to 15C then up to 24C"),
    ])
    assert [m.kind for m in night.marks] == [PHASE_KIND, BY_HAND]


def test_the_headline_does_not_claim_the_ones_you_made(plan):
    """The number sits under the word Autopilot. Counting a tap you made would
    make the one number on the screen the least true thing on it."""
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND),
        ev(at, "temperature"),
        ev(at + timedelta(hours=3), "temperature"),
    ])
    assert night.adjustments == 1, "but both are still in the breakdown"
    assert night.counted(BY_HAND) == 1


def test_a_failed_command_is_not_an_adjustment(plan):
    """Warnings and errors carry the same kinds. Only what landed counts."""
    at = plan.steps[0].starts_at
    night = build(plan, [ev(at, "temperature", "Could not send", level="error")])
    assert night.adjustments == 0


# --- The chart ------------------------------------------------------------------


def test_the_track_is_distance_from_whatever_stage_was_running(plan):
    """Not absolute degrees. A night stepping 19 to 26 has no single line to sit
    near, and drawing it against one makes a perfect night look like a climb."""
    night = build(plan, [], samples(plan, offset=0.4))
    assert night.track, "nothing to draw"
    assert all(abs(p.offset_c - 0.4) < 0.01 for p in night.track)


def test_getting_ready_is_measured_against_the_first_stage(plan):
    """There is no step before bedtime, but there is still a target: that whole
    stretch is the bed arriving at the temperature stage one will ask for."""
    early = (plan.precool_at or plan.bedtime_at) + timedelta(minutes=5)
    night = build(plan, [], [Sample(at=early, watts=170.0, return_c=plan.steps[0].temp_c + 3)])
    assert [round(p.offset_c) for p in night.track] == [3]


def test_a_dot_with_nothing_measuring_it_has_nowhere_to_sit(plan):
    """Rather than being drawn at an invented height."""
    at = plan.steps[0].starts_at
    night = build(plan, [ev(at, PHASE_KIND), ev(at, "temperature")])
    assert night.marks[0].offset_c is None


def test_a_night_with_no_probe_readings_still_counts_what_it_did(plan):
    at = plan.steps[0].starts_at
    night = build(plan, [ev(at, PHASE_KIND), ev(at, "temperature")])
    assert night.adjustments == 1
    assert not night.measured, "and says there is no chart rather than drawing an empty one"
    assert night.boosts == []


# --- On target, and the invented three -------------------------------------------


def test_on_target_is_the_share_of_the_night_within_half_a_degree(plan):
    assert build(plan, [], samples(plan, offset=0.2)).on_target == 100
    assert build(plan, [], samples(plan, offset=1.4)).on_target == 0


def test_the_boosts_are_invented_but_they_are_not_random(plan):
    """Same night, same figures. A random number looks broken the first time two
    reloads disagree, and this screen is reloaded every morning."""
    rows = samples(plan, offset=0.3)
    once = build(plan, [], rows).boosts
    twice = build(plan, [], rows).boosts
    assert once == twice
    assert [b.key for b in once] == ["deep", "rem"]
    assert all(0 < b.percent <= autopilot.BOOST_CEILING for b in once)


def test_a_badly_tracked_night_earns_less(plan):
    """Which is the whole reason they are derived from the water rather than
    generated. They move with the night."""
    good = {b.key: b.percent for b in build(plan, [], samples(plan, offset=0.1)).boosts}
    poor = {b.key: b.percent for b in build(plan, [], samples(plan, offset=1.8)).boosts}
    assert good["deep"] > poor["deep"]
    assert good["rem"] > poor["rem"]


def test_a_night_the_bed_never_tracked_earns_nothing_rather_than_a_floor(plan):
    assert build(plan, [], samples(plan, offset=4.0)).boosts == []


def test_getting_ready_earns_one_only_when_it_actually_arrived(plan):
    rows = samples(plan, offset=0.3)
    arrived = PreconditionRow(
        at=plan.bedtime_at, mode="turbo", target_c=19, seconds=1200, reached=True
    )
    gave_up = arrived._replace(reached=False)
    assert any(b.key == "ready" for b in build(plan, [], rows, ready=arrived).boosts)
    assert not any(b.key == "ready" for b in build(plan, [], rows, ready=gave_up).boosts)


# --- The rest of the night --------------------------------------------------------


def test_a_missed_stage_is_named(plan):
    night = autopilot.build(plan, [], [], {"stage:deep", "stage:rem"}, None)
    assert night.stages_landed == 2
    assert night.missed == ["Wake"]


def test_only_the_things_worth_a_look_come_through_as_notes(plan):
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND, "Deep: 19C"),
        ev(at, "blaster", "The blaster is not answering", level="error"),
        ev(at, "plug", "The plug is slow", level="warning"),
    ])
    assert night.notes == ["The blaster is not answering", "The plug is slow"]


# --- Which night, and what to call it ---------------------------------------------


def test_it_describes_the_night_that_has_finished_not_the_one_coming(plan):
    """Asked at five in the afternoon, the answer is the night that ended that
    morning. The one starting in a few hours has not happened."""
    from hydrosnooze.scheduler import Scheduler

    schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=[0, 1, 2, 3, 4],
        stages=[SleepStage(Stage.DEEP, 240, 19), SleepStage(Stage.WAKE, 60, 26)],
    )
    picked = Scheduler().last_finished(schedule, datetime(2026, 9, 11, 17, 19))

    assert picked is not None
    assert picked.bedtime_at.date() == date(2026, 9, 10), "it went to bed on Thursday"
    assert picked.wake_at.date() == date(2026, 9, 11), "and got up on Friday"


def test_a_night_still_running_is_not_reported_on(plan):
    from hydrosnooze.scheduler import Scheduler

    schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=[0, 1, 2, 3, 4],
        stages=[SleepStage(Stage.DEEP, 240, 19), SleepStage(Stage.WAKE, 60, 26)],
    )
    # Three in the morning, mid-night. The answer is the night before, not this one.
    picked = Scheduler().last_finished(schedule, datetime(2026, 9, 11, 3, 0))
    assert picked is not None and picked.wake_at.date() == date(2026, 9, 10)


def test_the_night_carries_both_of_its_dates(plan):
    """Which is the whole reason the label can say so. A night has an evening and
    a morning, and naming only one of them is how "Friday" ended up on a report
    about a night that finished on Friday morning."""
    night = build(plan, [])
    assert night.starts_at.date() != night.wake_at.date()
    assert night.starts_at < night.wake_at


# --- The evening of 10 September ---------------------------------------------------


def test_one_reason_cannot_claim_an_evening_of_somebody_tapping(plan):
    """Liam's first real Autopilot screen, replayed.

    Thursday evening he sat with the app changing the temperature: fourteen
    commands between 21:40 and 22:00, a mode press and a rail-and-count at a
    time. One pre-cool event landed in the middle of it, and because a reason
    used to claim everything inside its window, all fourteen were reported as
    "getting the bed ready" — a job worth exactly two commands. The screen
    credited Autopilot with his tapping, and the row those taps belonged in read
    low by a dozen.
    """
    start = plan.bedtime_at - timedelta(minutes=50)
    events = [ev(start, autopilot.AMBIENT_KIND, "Pre-cooling in quiet to 27C")]
    # A mode press and a rail-and-count a minute apart, seven times over.
    for i in range(7):
        at = start + timedelta(minutes=i * 3)
        events.append(ev(at, "mode", "Set mode by hand"))
        events.append(ev(at + timedelta(minutes=1), "temperature", "Railed up by hand"))

    night = build(plan, events)

    assert night.counted(autopilot.AMBIENT_KIND) == 2, "one job is worth two commands"
    assert night.counted(BY_HAND) == 12, "and the other twelve were a person"
    assert night.adjustments == 2, "the headline counts what Autopilot did"


def test_a_reason_still_gets_both_of_its_commands(plan):
    """The cap is two rather than one, because a boundary that changes mode sends
    a mode press and then a rail-and-count."""
    at = plan.steps[1].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND, "REM: 22C in quiet"),
        ev(at + timedelta(seconds=5), "mode", "Set mode to quiet"),
        ev(at + timedelta(seconds=45), "temperature", "Railed to 15C then up to 22C"),
    ])
    assert night.counted(PHASE_KIND) == 2


def test_a_slow_power_on_does_not_lose_the_boundary(plan):
    """A stage boundary that finds the unit off now spends two patient minutes on
    power_on before it sets anything, which is why the window is not tight."""
    at = plan.steps[0].starts_at
    night = build(plan, [
        ev(at, PHASE_KIND, "Deep: 19C in quiet"),
        ev(at + timedelta(minutes=2, seconds=20), "temperature", "Railed to 15C then up to 19C"),
    ])
    assert night.counted(PHASE_KIND) == 1
    assert night.counted(BY_HAND) == 0


def test_each_reason_gets_its_own_pair_rather_than_the_first_one_taking_four(plan):
    """Two corrections half an hour apart are two jobs, not one greedy one."""
    first = plan.steps[0].starts_at + timedelta(hours=1)
    second = first + timedelta(minutes=30)
    events = []
    for at in (first, second):
        events += [
            ev(at, RESPONSE_KIND, "Swapped to hold it quietly"),
            ev(at, "mode"),
            ev(at, "temperature"),
        ]
    night = build(plan, events)
    assert night.counted(RESPONSE_KIND) == 4
    assert night.counted(BY_HAND) == 0
