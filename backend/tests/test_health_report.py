"""The Health Report: one night, the way the screen draws it.

Built from the invented fixtures, and from copies of them moved in time where a
test needs a history: the week along the top, what "usual" means for Routine and
for the vitals.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from withings_fake import intervals, summaries

from hydrosnooze.db import Database
from hydrosnooze.withings import health, parse

NIGHTS = {s["date"]: s for s in summaries()}
DAY = 86400


def parsed(wake_on: str) -> parse.Night:
    return parse.night(NIGHTS[wake_on], intervals(wake_on))


def moved(night: parse.Night, days: int, *, minutes: int = 0, new_id: int | None = None,
          **data: object) -> parse.Night:
    """The same night, `days` earlier or later, optionally later in the evening."""
    by = days * DAY + minutes * 60
    return replace(
        night,
        id=new_id if new_id is not None else night.id + days * 1000 + minutes,
        wake_on=(date.fromisoformat(night.wake_on) + timedelta(days=days)).isoformat(),
        start_at=night.start_at + by,
        end_at=night.end_at + by,
        modified=night.modified + by,
        data={**night.data, **data},
        events={k: [t + by for t in v] for k, v in (night.events or {}).items()},
        stages=tuple(
            replace(s, start_at=s.start_at + by, end_at=s.end_at + by) for s in night.stages
        ),
        minutes=tuple(replace(m, at=m.at + by) for m in night.minutes),
    )


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "s.db")
    yield database
    database.close()


@pytest.fixture
def week(db):
    for wake_on in NIGHTS:
        db.save_sleep_night(parsed(wake_on))
    return db


def test_no_sleep_at_all_is_no_report(db):
    assert health.report(db) is None


def test_the_latest_night_by_default_in_a_week_that_starts_on_sunday(week):
    built = health.report(week)
    assert built["night"]["wake_on"] == "2026-10-25"
    days = built["week"]
    assert days[0]["date"] == "2026-10-25"  # a Sunday
    assert [d["has_night"] for d in days] == [True] + [False] * 6
    assert built["earliest"] == "2026-10-22" and built["latest"] == "2026-10-25"


def test_the_week_carries_each_nights_score(week):
    built = health.report(week, "2026-10-22")
    days = {d["date"]: d for d in built["week"]}
    assert [d["date"] for d in built["week"]][0] == "2026-10-18"
    for wake_on in ("2026-10-22", "2026-10-23", "2026-10-24"):
        assert days[wake_on]["score"] == NIGHTS[wake_on]["data"]["sleep_score"]
    assert days["2026-10-20"] == {"date": "2026-10-20", "score": None, "has_night": False}


def test_a_morning_with_no_night_still_has_its_week(week):
    built = health.report(week, "2026-10-20")
    assert built["night"] is None
    assert len(built["week"]) == 7


@pytest.mark.parametrize("wake_on", list(NIGHTS))
def test_the_numbers_are_withings_own(week, wake_on):
    n = health.report(week, wake_on)["night"]
    d = NIGHTS[wake_on]["data"]
    assert n["score"]["value"] == d["sleep_score"]
    assert n["tiles"]["quality"]["percent"] == round(d["sleep_efficiency"] * 100)
    assert n["tiles"]["time_slept"]["seconds"] == d["total_sleep_time"]
    assert n["totals"] == {
        "awake": d["wakeupduration"],
        "light": d["lightsleepduration"],
        "deep": d["deepsleepduration"],
        "rem": d["remsleepduration"],
    }
    assert n["rem"]["percent"] == round(100 * d["remsleepduration"] / d["total_sleep_time"])
    assert n["rem"]["met"] == (d["remsleepduration"] >= health.REM_TARGET_S)
    assert n["deep"]["met"] == (d["deepsleepduration"] >= health.DEEP_TARGET_S)


@pytest.mark.parametrize(
    ("value", "bands", "verdict"),
    [
        (97, health.SCORE_BANDS, "good"),
        (78, health.SCORE_BANDS, "fair"),
        (100, health.PERCENT_BANDS, "excellent"),
        (89, health.PERCENT_BANDS, "good"),
        (87, health.PERCENT_BANDS, "good"),
        (51, health.PERCENT_BANDS, "low"),
        (7 * 3600 + 34 * 60, health.SLEPT_BANDS_S, "good"),
        (None, health.SCORE_BANDS, None),
    ],
)
def test_the_verdicts_agree_with_the_screenshots(value, bands, verdict):
    assert health._verdict(value, bands)["verdict"] == verdict


def test_fell_asleep_and_woke_up_come_from_the_nights_own_events(week):
    n = health.report(week, "2026-10-22")["night"]
    s = NIGHTS["2026-10-22"]
    tz = "+01:00"
    assert n["fell_asleep_at"].endswith(tz)
    fell = parse.events(s["data"]["night_events"], s["startdate"])["asleep"][0]
    assert fell - s["startdate"] == s["data"]["sleep_latency"]
    assert n["fell_asleep_at"] == health._iso(fell, health._zone("Europe/London"))


def test_the_stages_are_the_hypnogram_in_order_with_gaps_for_out_of_bed(week):
    n = health.report(week, "2026-10-24")["night"]
    assert n["stages"][0]["starts_at"] == n["in_bed"]["starts_at"]
    assert n["stages"][-1]["ends_at"] == n["in_bed"]["ends_at"]
    assert {s["stage"] for s in n["stages"]} <= {"awake", "light", "deep", "rem"}
    assert len(n["out_of_bed"]) == 2
    ends = {s["ends_at"] for s in n["stages"]}
    starts = {s["starts_at"] for s in n["stages"]}
    for gap in n["out_of_bed"]:
        assert gap["starts_at"] in ends and gap["ends_at"] in starts


def test_the_night_the_clocks_go_back_says_which_one_thirty_it_means(week):
    n = health.report(week, "2026-10-25")["night"]
    assert n["in_bed"]["starts_at"] == "2026-10-24T22:50:00+01:00"
    assert n["in_bed"]["ends_at"] == "2026-10-25T07:05:00+00:00"
    assert n["out_of_bed"] == [
        {"starts_at": "2026-10-25T01:20:00+00:00", "ends_at": "2026-10-25T01:32:00+00:00"}
    ]


# --- Routine -----------------------------------------------------------------------


def test_routine_is_learning_until_three_nights_before_it(week):
    routine = health.report(week, "2026-10-22")["night"]["tiles"]["routine"]
    assert routine["percent"] is None
    assert routine["verdict"] == "learning" and routine["nights_needed"] == 3


def test_the_same_times_every_night_is_a_perfect_routine(db):
    night = parsed("2026-10-22")
    for days in (-3, -2, -1, 0):
        db.save_sleep_night(moved(night, days))
    routine = health.report(db, "2026-10-22")["night"]["tiles"]["routine"]
    assert routine["percent"] == 100 and routine["verdict"] == "excellent"


def test_two_and_a_half_hours_late_to_bed_and_up_is_no_routine_at_all(db):
    night = parsed("2026-10-22")
    for days in (-3, -2, -1):
        db.save_sleep_night(moved(night, days))
    db.save_sleep_night(moved(night, 0, minutes=150))
    routine = health.report(db, "2026-10-22")["night"]["tiles"]["routine"]
    assert routine["percent"] == 0 and routine["verdict"] == "low"


def test_half_an_hour_off_costs_a_little(db):
    night = parsed("2026-10-22")
    for days in (-3, -2, -1):
        db.save_sleep_night(moved(night, days))
    db.save_sleep_night(moved(night, 0, minutes=30))
    # Fifteen minutes past the free fifteen, out of the hundred and five that
    # take it to nothing, on both halves.
    expected = round(100 * (1 - 15 / 105))
    assert health.report(db, "2026-10-22")["night"]["tiles"]["routine"]["percent"] == expected


# --- Vitals ------------------------------------------------------------------------


def test_the_vitals_are_learning_until_seven_nights_before(week):
    vitals = health.report(week, "2026-10-25")["night"]["vitals"]
    for v in vitals.values():
        assert v["verdict"] == "learning" and v["nights_needed"] == 4
        assert v["range"] is None
    assert vitals["heart_rate"]["value"] == NIGHTS["2026-10-25"]["data"]["hr_average"]
    assert vitals["breath_rate"]["unit"] == "/min"
    assert isinstance(vitals["breath_rate"]["value"], float)


def test_a_night_like_the_others_is_in_range_and_a_racing_heart_is_not(db):
    night = parsed("2026-10-22")
    for days in range(-10, 0):
        db.save_sleep_night(moved(night, days))
    db.save_sleep_night(night)
    vitals = health.report(db, "2026-10-22")["night"]["vitals"]
    assert {v["verdict"] for v in vitals.values()} == {"in_range"}

    db.save_sleep_night(moved(night, 1, hr_average=90))
    racing = health.report(db, "2026-10-23")["night"]["vitals"]["heart_rate"]
    assert racing["verdict"] == "above" and racing["label"] == "Above usual"


def test_hrv_is_averaged_over_sleep_and_ignores_the_minutes_it_could_not_measure(db):
    night = parsed("2026-10-22")
    db.save_sleep_night(night)
    asleep = [m.rmssd for m in night.minutes if m.state in parse.SLEEPING and m.rmssd is not None]
    hrv = health.report(db, "2026-10-22")["night"]["vitals"]["hrv"]["value"]
    assert hrv == round(sum(asleep) / len(asleep))
