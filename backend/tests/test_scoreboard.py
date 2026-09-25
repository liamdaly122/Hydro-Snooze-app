"""The scoreboard: each setting each part has run at, and the sleep on it.

Built from nights written down by hand here, with the mat's numbers chosen, so
every average and every verdict can be checked on paper. The one rule that
matters most: it never calls two settings apart on less than the night-to-night
swing could explain.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hydrosnooze.db import Database
from hydrosnooze.trials import NightRun, PartRun
from hydrosnooze.withings import parse, scoreboard

LONDON = ZoneInfo("Europe/London")
TODAY = date(2026, 9, 25)
NOON = datetime.combine(TODAY, time(12, 0))


def run(wake_on: date, *, deep=17, rem=20, drift=19, room=19.0) -> NightRun:
    """A night that ran these settings, each held all the way through."""
    bed = datetime.combine(wake_on - timedelta(days=1), time(23, 0))

    def part(name, set_c, starts, minutes):
        return PartRun(
            part=name, starts_at=bed + timedelta(minutes=starts),
            ends_at=bed + timedelta(minutes=starts + minutes), set_c=set_c,
            held=1.0, bed_c=set_c + 0.5, by_hand=False,
        )

    return NightRun(
        wake_on=wake_on.isoformat(), bedtime_at=bed, wake_at=bed + timedelta(hours=8),
        parts=(part("drift", drift, 0, 30), part("deep", deep, 30, 240),
               part("rem", rem, 270, 180), part("wake", 26, 450, 30)),
        room_c=room,
    )


def mat(wake_on: date, *, deep_m=90, rem_m=100, latency_m=15, asleep_h=7.0, awake_m=20) -> parse.Night:
    start = int(datetime.combine(wake_on - timedelta(days=1), time(23, 10), LONDON).timestamp())
    end = start + 8 * 3600
    return parse.Night(
        id=start, wake_on=wake_on.isoformat(), start_at=start, end_at=end,
        timezone="Europe/London", modified=end, completed=True,
        data={
            "deepsleepduration": deep_m * 60, "remsleepduration": rem_m * 60,
            "sleep_latency": latency_m * 60, "total_sleep_time": int(asleep_h * 3600),
            "wakeupduration": awake_m * 60,
        },
        events=None,
    )


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "s.db")
    yield database
    database.close()


def night(db, days_ago: int, run_kw=None, mat_kw=None, *, on_mat=True):
    morning = TODAY - timedelta(days=days_ago)
    db.save_night_run(run(morning, **(run_kw or {})), NOON)
    if on_mat:
        db.save_sleep_night(mat(morning, **(mat_kw or {})))


def part(built, name):
    return next(p for p in built["parts"] if p["part"] == name)


# Night-to-night swing, the same for both settings: +-10 minutes around the middle.
SWING = [-10, 10, -5, 5, 0, -10, 10, -5, 5, 0]


def test_nothing_recorded_is_said_plainly(db):
    built = scoreboard.scoreboard(db, TODAY)
    assert built["recorded"] == built["nights"] == built["tests"] == 0
    assert [p["part"] for p in built["parts"]] == ["deep", "rem", "drift"]
    assert all(p["verdict"] == "empty" and p["settings"] == [] for p in built["parts"])


def test_one_setting_is_scored_but_compared_with_nothing(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, mat_kw={"deep_m": 90 + d})
    deep = part(scoreboard.scoreboard(db, TODAY), "deep")
    assert deep["verdict"] == "one_setting" and deep["leader_c"] is None
    [only] = deep["settings"]
    assert only["set_c"] == 17 and only["nights"] == 10
    assert only["mean_s"] == 90 * 60
    assert only["room_c"] == 19.0 and only["bed_c"] == 17.5
    assert only["awake_s"] == 20 * 60 and only["asleep_after_s"] == 15 * 60


def test_a_gap_well_past_the_swing_is_called(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, {"deep": 17}, {"deep_m": 90 + d})
        night(db, i + 20, {"deep": 16}, {"deep_m": 110 + d})
    deep = part(scoreboard.scoreboard(db, TODAY), "deep")
    assert deep["verdict"] == "clear"
    assert (deep["leader_c"], deep["runner_c"]) == (16, 17)
    assert deep["gap_s"] == 20 * 60 and deep["swing_s"] < deep["gap_s"]


def test_a_gap_inside_the_swing_is_not_sure_yet(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, {"deep": 17}, {"deep_m": 90 + d})
        night(db, i + 20, {"deep": 16}, {"deep_m": 93 + d})
    deep = part(scoreboard.scoreboard(db, TODAY), "deep")
    assert deep["verdict"] == "not_sure"
    assert deep["leader_c"] == 16 and deep["gap_s"] == 3 * 60
    assert deep["swing_s"] >= deep["gap_s"]


def test_too_few_nights_at_a_setting_is_not_compared(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, {"deep": 17}, {"deep_m": 90 + d})
    for i in range(scoreboard.SETTING_NEEDS - 1):
        night(db, i + 20, {"deep": 16}, {"deep_m": 150})
    deep = part(scoreboard.scoreboard(db, TODAY), "deep")
    assert deep["verdict"] == "not_sure" and deep["leader_c"] is None
    assert [s["nights"] for s in deep["settings"]] == [4, 10]


def test_falling_asleep_is_better_shorter(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, {"drift": 19}, {"latency_m": 30 + d // 5})
        night(db, i + 20, {"drift": 21}, {"latency_m": 12 + d // 5})
    drift = part(scoreboard.scoreboard(db, TODAY), "drift")
    assert drift["verdict"] == "clear" and drift["leader_c"] == 21


def test_each_part_is_scored_on_its_own_setting(db):
    for i in range(6):
        night(db, i + 1, {"deep": 17, "rem": 20})
        night(db, i + 20, {"deep": 17, "rem": 21})
    built = scoreboard.scoreboard(db, TODAY)
    assert [s["set_c"] for s in part(built, "deep")["settings"]] == [17]
    assert [s["set_c"] for s in part(built, "rem")["settings"]] == [20, 21]


# --- Which nights count ---------------------------------------------------------------


def deep_changed(r: NightRun, **change) -> NightRun:
    return replace(r, parts=tuple(replace(p, **change) if p.part == "deep" else p for p in r.parts))


def test_a_part_changed_by_hand_or_not_held_is_left_out_of_that_part_only(db):
    night(db, 1)
    night(db, 2)
    touched = deep_changed(run(TODAY - timedelta(days=3)), by_hand=True)
    loose = deep_changed(run(TODAY - timedelta(days=4)), held=0.3)
    for r in (touched, loose):
        db.save_night_run(r, NOON)
        db.save_sleep_night(mat(date.fromisoformat(r.wake_on)))
    built = scoreboard.scoreboard(db, TODAY)
    assert part(built, "deep")["settings"][0]["nights"] == 2
    assert part(built, "rem")["settings"][0]["nights"] == 4


def test_no_night_on_the_mat_or_a_nap_is_not_a_night(db):
    night(db, 1)
    night(db, 2, on_mat=False)
    night(db, 3, mat_kw={"asleep_h": 2.5})
    built = scoreboard.scoreboard(db, TODAY)
    assert built["recorded"] == 3 and built["nights"] == 1


def test_only_the_last_four_months(db):
    night(db, 1)
    night(db, scoreboard.SCORE_DAYS + 5)
    assert scoreboard.scoreboard(db, TODAY)["nights"] == 1


def test_test_nights_are_counted_where_they_were_tests(db):
    for i in range(3):
        morning = TODAY - timedelta(days=i + 1)
        db.save_night_run(replace(run(morning, deep=16), test_part="deep", test_offset_c=-1), NOON)
        db.save_sleep_night(mat(morning))
    built = scoreboard.scoreboard(db, TODAY)
    assert built["tests"] == 3
    assert part(built, "deep")["settings"][0]["tests"] == 3
    assert part(built, "rem")["settings"][0]["tests"] == 0, "those nights ran REM as usual"


def test_the_room_is_carried_beside_each_setting(db):
    for i in range(5):
        night(db, i + 1, {"deep": 17, "room": 18.0 + i * 0.5})
        night(db, i + 20, {"deep": 16, "room": 22.0})
    settings = part(scoreboard.scoreboard(db, TODAY), "deep")["settings"]
    assert [(s["set_c"], s["room_c"]) for s in settings] == [(16, 22.0), (17, 19.0)]
