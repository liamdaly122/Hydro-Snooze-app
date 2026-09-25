"""When I really sleep, against the parts of the night the bed runs.

Every night here is built by hand, minute by minute, so the answer each test
expects can be worked out on paper: asleep this long after lights out, deep
sleep mostly done this long after that.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hydrosnooze.db import Database
from hydrosnooze.models import Schedule
from hydrosnooze.withings import parse, timing
from hydrosnooze.withings.parse import AWAKE, DEEP, LIGHT, REM

LONDON = ZoneInfo("Europe/London")

#: The last morning in every history below. A Friday.
LAST = date(2026, 9, 25)


def night_of(wake_on: date, runs: list[tuple[int | None, int]], *, starts: time = time(22, 30),
             night_id: int | None = None) -> parse.Night:
    """A night of `runs`, each (state, minutes), from `starts` the evening before.

    A state of None is time out of bed: no minutes, and a gap between stages.
    """
    evening = wake_on - timedelta(days=1) if starts.hour >= 12 else wake_on
    start = int(datetime.combine(evening, starts, LONDON).timestamp())
    stages, minutes, t = [], [], start
    for state, length in runs:
        if state is not None:
            stages.append(parse.Stage(t, t + length * 60, state))
            minutes += [parse.Minute(t + 60 * i, state) for i in range(length)]
        t += length * 60
    asleep = sum(length for state, length in runs if state in parse.SLEEPING) * 60
    return parse.Night(
        id=night_id or start,
        wake_on=wake_on.isoformat(),
        start_at=start,
        end_at=t,
        timezone="Europe/London",
        modified=t + 120,
        completed=True,
        data={"total_sleep_time": asleep},
        events=None,
        stages=tuple(stages),
        minutes=tuple(minutes),
    )


#: Asleep 20 minutes after lights out, and 100 minutes of deep sleep with the
#: 80th of them at 139 minutes after lights out.
TYPICAL = [
    (AWAKE, 20),   # 0-20
    (DEEP, 60),    # 20-80: deep minutes 1-60
    (LIGHT, 40),   # 80-120
    (DEEP, 20),    # 120-140: deep minutes 61-80, the 80th at minute 139
    (REM, 30),     # 140-170
    (DEEP, 20),    # 170-190: deep minutes 81-100
    (LIGHT, 120),  # 190-310
    (REM, 60),     # 310-370
    (LIGHT, 90),   # 370-460
    (AWAKE, 20),   # 460-480
]
ASLEEP_MIN = 20
DEEP_DONE_MIN = 139


def every_day(**kw) -> Schedule:
    """The default night, 22:30 to 06:30, on every morning of the week."""
    return Schedule(days_of_week=list(range(7)), **kw)


def history(db: Database, count: int, runs=TYPICAL, *, last: date = LAST, **kw) -> None:
    for i in range(count):
        db.save_sleep_night(night_of(last - timedelta(days=i), runs, **kw))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "s.db")
    yield database
    database.close()


def ends(schedule: Schedule) -> dict[str, int]:
    out, cursor = {}, 0
    for s in schedule.stages:
        cursor += s.duration_minutes
        out[s.stage.value] = cursor
    return out


def boundary(built: dict, part: str) -> dict:
    return next(b for b in built["boundaries"] if b["part"] == part)


# --- Before there is anything to say ---------------------------------------------------


def test_no_nights_still_answers_with_the_schedule(db):
    built = timing.timing(db, every_day(), LAST)
    assert built["nights"] == 0
    assert built["profile"] is None and built["boundaries"] == []
    assert built["lights_out"] == "22:30" and built["wake"] == "06:30"
    assert [p["part"] for p in built["parts"]] == ["drift", "deep", "rem", "wake"]
    assert built["parts"][-1]["ends_min"] == built["night_minutes"] == 480


def test_two_nights_are_not_a_pattern(db):
    history(db, 2)
    built = timing.timing(db, every_day(), LAST)
    assert built["nights"] == 2
    assert built["profile"] is None and built["boundaries"] == []


def test_a_pattern_is_drawn_from_three_but_nothing_is_suggested_before_fourteen(db):
    history(db, 13)
    built = timing.timing(db, every_day(), LAST)
    assert built["nights"] == 13
    assert built["profile"] is not None
    for b in built["boundaries"]:
        assert b["suggest_min"] is None
    assert boundary(built, "drift")["measured"]["median_min"] == ASLEEP_MIN
    assert boundary(built, "deep")["measured"]["median_min"] == DEEP_DONE_MIN


# --- What is measured ------------------------------------------------------------------


def test_asleep_and_deep_mostly_done_are_measured_from_lights_out(db):
    history(db, 14)
    built = timing.timing(db, every_day(), LAST)
    drift, deep = boundary(built, "drift"), boundary(built, "deep")
    assert drift["measured"] == {"median_min": 20, "low_min": 20, "high_min": 20}
    assert deep["measured"] == {"median_min": 139, "low_min": 139, "high_min": 139}
    assert drift["steady"] is True and deep["steady"] is True


def test_going_to_bed_late_counts_against_the_clock(db):
    """In bed an hour after lights out: the Deep part started without me."""
    history(db, 14, starts=time(23, 30))
    built = timing.timing(db, every_day(), LAST)
    assert boundary(built, "drift")["measured"]["median_min"] == 60 + ASLEEP_MIN
    assert boundary(built, "deep")["measured"]["median_min"] == 60 + DEEP_DONE_MIN


def test_the_profile_says_how_often_each_stage_happens_at_each_point(db):
    history(db, 3)
    built = timing.timing(db, every_day(), LAST)
    profile = built["profile"]
    assert len(profile["deep"]) == 480 // timing.BIN_MIN
    assert profile["awake"][0] == 1.0  # 0-10 minutes: awake every night
    assert profile["deep"][3] == 1.0  # 30-40: deep every night
    assert profile["light"][10] == 1.0  # 100-110
    assert profile["rem"][32] == 1.0  # 320-330
    assert profile["deep"][14] == 0.0 and profile["rem"][14] == 1.0  # 140-150


def test_a_bin_split_between_two_states_is_shared_between_them(db):
    runs = [(AWAKE, 5), (DEEP, 5), (LIGHT, 470)]
    history(db, 3, runs)
    profile = timing.timing(db, every_day(), LAST)["profile"]
    assert profile["awake"][0] == 0.5 and profile["deep"][0] == 0.5


def test_time_out_of_bed_is_in_no_stage(db):
    runs = [(AWAKE, 10), (DEEP, 60), (None, 10), (LIGHT, 400)]
    history(db, 3, runs)
    profile = timing.timing(db, every_day(), LAST)["profile"]
    assert sum(profile[k][7] for k in ("deep", "rem", "light", "awake")) == 0


# --- Which nights count ----------------------------------------------------------------


def test_only_mornings_the_schedule_runs_on(db):
    history(db, 14)  # 12 to 25 September
    weekdays = timing.timing(db, Schedule(days_of_week=[0, 1, 2, 3, 4]), LAST)
    assert weekdays["nights"] == 10


def test_a_schedule_with_no_days_takes_every_morning(db):
    history(db, 5)
    assert timing.timing(db, Schedule(days_of_week=[]), LAST)["nights"] == 5


def test_a_nap_is_not_a_night_and_the_longer_one_wins_a_morning(db):
    history(db, 3)
    nap = night_of(LAST, [(LIGHT, 60), (DEEP, 30)], starts=time(14, 0), night_id=1)
    db.save_sleep_night(nap)
    built = timing.timing(db, every_day(), LAST)
    assert built["nights"] == 3
    # A short night on a morning of its own is left out too.
    db.save_sleep_night(night_of(LAST - timedelta(days=5), [(DEEP, 30), (LIGHT, 60)]))
    assert timing.timing(db, every_day(), LAST)["nights"] == 3


def test_only_the_last_thirty_within_sixty_days(db):
    history(db, 40)
    assert timing.timing(db, every_day(), LAST)["nights"] == timing.TIMING_NIGHTS
    old = LAST - timedelta(days=100)
    assert timing.timing(db, every_day(), old)["nights"] == 0


def test_a_lights_out_after_midnight_lands_on_the_wake_morning(db):
    schedule = every_day(bed_time=time(0, 30), wake_time=time(8, 30))
    history(db, 3, starts=time(0, 30))
    built = timing.timing(db, schedule, LAST)
    assert boundary(built, "drift")["measured"]["median_min"] == ASLEEP_MIN


# --- What is suggested -----------------------------------------------------------------


def test_boundaries_move_to_the_sleep_in_the_schedule_screens_steps(db):
    history(db, 14)
    schedule = every_day()
    now = ends(schedule)
    built = timing.timing(db, schedule, LAST)
    drift, deep = boundary(built, "drift"), boundary(built, "deep")
    assert drift["ends_min"] == now["drift"] and deep["ends_min"] == now["deep"]
    # Whole steps from where each boundary is now, as near the sleep as that gets.
    for b, target in ((drift, ASLEEP_MIN), (deep, DEEP_DONE_MIN)):
        moved = b["suggest_min"] - b["ends_min"]
        assert moved % timing.STEP_MIN == 0
        assert abs(b["suggest_min"] - target) <= timing.STEP_MIN / 2


def test_taking_the_suggestion_leaves_nothing_to_suggest(db):
    history(db, 14)
    schedule = every_day()
    wanted = {b["part"]: b["suggest_min"] for b in timing.timing(db, schedule, LAST)["boundaries"]}
    new_ends = {**ends(schedule), **wanted}
    starts = 0
    stages = []
    for s in schedule.stages:
        stages.append(replace(s, duration_minutes=new_ends[s.stage.value] - starts))
        starts = new_ends[s.stage.value]
    taken = replace(schedule, stages=stages)
    assert ends(taken) == new_ends
    again = timing.timing(db, taken, LAST)
    assert [b["suggest_min"] for b in again["boundaries"]] == [None, None]


def test_nights_that_disagree_suggest_nothing_and_say_so(db):
    early = TYPICAL
    late = [(AWAKE, 20), (LIGHT, 120)] + [(DEEP, 100)] + [(LIGHT, 240)]
    for i in range(14):
        db.save_sleep_night(night_of(LAST - timedelta(days=i), early if i % 2 else late))
    built = timing.timing(db, every_day(), LAST)
    deep = boundary(built, "deep")
    assert deep["steady"] is False and deep["suggest_min"] is None
    assert deep["measured"]["high_min"] - deep["measured"]["low_min"] > timing.STEADY_SPREAD_MIN
    # Falling asleep still agrees, so that one can still move.
    assert boundary(built, "drift")["steady"] is True


def test_rem_is_never_squeezed_out(db):
    late = [(AWAKE, 20), (LIGHT, 400), (DEEP, 60)]
    history(db, 14, late)
    schedule = every_day()
    built = timing.timing(db, schedule, LAST)
    deep = boundary(built, "deep")
    assert deep["suggest_min"] is not None
    assert ends(schedule)["rem"] - deep["suggest_min"] >= timing.STEP_MIN


def test_drift_is_never_shorter_than_a_step(db):
    runs = [(DEEP, 100), (LIGHT, 380)]  # asleep the moment the light goes out
    history(db, 14, runs)
    built = timing.timing(db, every_day(), LAST)
    assert boundary(built, "drift")["suggest_min"] >= timing.STEP_MIN


def test_the_lights_out_is_the_schedules_on_the_night_the_clocks_go_back(db):
    """25 October 2026: 22:30 the evening before is still summer time."""
    history(db, 3, last=date(2026, 10, 25))
    built = timing.timing(db, every_day(), date(2026, 10, 25))
    assert boundary(built, "drift")["measured"]["median_min"] == ASLEEP_MIN
