"""Turning what Withings sends into nights, and keeping them.

Against the invented fixtures, which are held to every rule seven real nights on
the mat obeyed. See scripts/withings-fixtures.py and docs/withings.md.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from withings_fake import intervals, summaries

from hydrosnooze.db import Database
from hydrosnooze.withings import parse

NIGHTS = summaries()


def parsed(i: int) -> parse.Night:
    s = NIGHTS[i]
    return parse.night(s, intervals(s["date"]))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "s.db")
    yield database
    database.close()


# --- night_events ----------------------------------------------------------------


@pytest.mark.parametrize("i", range(len(NIGHTS)))
def test_night_events_decode_to_the_times_the_summary_agrees_with(i):
    night = parsed(i)
    d = NIGHTS[i]["data"]
    ev = night.events
    assert ev is not None
    assert ev["into_bed"][0] == night.start_at
    assert ev["out_of_bed"][-1] == night.end_at
    assert ev["asleep"][0] - night.start_at == d["sleep_latency"]
    assert night.end_at - ev["woke"][-1] == d["wakeup_latency"]
    assert len(ev["woke"]) - 1 == d["wakeupcount"]


def test_night_events_arrive_as_json_inside_a_string():
    raw = NIGHTS[0]["data"]["night_events"]
    assert isinstance(raw, str)
    assert parse.events(raw, 1000) == parse.events(json.loads(raw), 1000)


def test_each_list_is_gaps_from_the_one_before_of_the_same_kind():
    ev = parse.events('{"1":[0,25500],"2":[1200,24600],"3":[24900,3600],"4":[25200,3600]}', 100)
    assert ev == {
        "into_bed": [100, 25600],
        "asleep": [1300, 25900],
        "woke": [25000, 28600],
        "out_of_bed": [25300, 28900],
    }


def test_no_events_and_nonsense_events_are_none_and_an_unknown_kind_is_passed_over():
    assert parse.events(None, 0) is None
    assert parse.events("not json", 0) is None
    assert parse.events('{"1":[0],"9":[5]}', 10) == {"into_bed": [10]}


# --- stages ------------------------------------------------------------------------


@pytest.mark.parametrize("i", range(len(NIGHTS)))
def test_an_interval_is_not_a_stage(i):
    night = parsed(i)
    raw = intervals(NIGHTS[i]["date"])
    assert len(night.stages) < len(raw) / 2
    for a, b in zip(night.stages, night.stages[1:]):
        assert not (a.state == b.state and a.end_at == b.start_at), "neighbours left unmerged"


@pytest.mark.parametrize("i", range(len(NIGHTS)))
def test_stages_add_up_to_the_summary_to_the_second(i):
    night = parsed(i)
    d = NIGHTS[i]["data"]
    per = {s: 0 for s in (parse.AWAKE, parse.LIGHT, parse.DEEP, parse.REM)}
    for st in night.stages:
        per[st.state] += st.end_at - st.start_at
    assert per[parse.AWAKE] == d["wakeupduration"]
    assert per[parse.LIGHT] == d["lightsleepduration"]
    assert per[parse.DEEP] == d["deepsleepduration"]
    assert per[parse.REM] == d["remsleepduration"]


def test_a_stage_never_swallows_time_out_of_bed():
    stages = parse.stages(
        [
            {"startdate": 0, "enddate": 60, "state": 1},
            {"startdate": 120, "enddate": 180, "state": 1},
        ]
    )
    assert [(s.start_at, s.end_at) for s in stages] == [(0, 60), (120, 180)]


# --- minutes -----------------------------------------------------------------------


@pytest.mark.parametrize("i", range(len(NIGHTS)))
def test_one_minute_a_row_for_every_minute_in_bed(i):
    night = parsed(i)
    assert len(night.minutes) * 60 == NIGHTS[i]["data"]["total_timeinbed"]
    assert all(isinstance(m.at, int) for m in night.minutes)
    assert [m.at for m in night.minutes] == sorted(m.at for m in night.minutes)


def test_zero_variability_is_no_reading_rather_than_zero():
    raw = [e for n in NIGHTS for e in intervals(n["date"])]
    zeros = sum(1 for e in raw for v in e["sdnn_1"].values() if v == 0)
    assert zeros > 0, "the fixtures should carry the zeros real nights do"
    minutes = [m for i in range(len(NIGHTS)) for m in parsed(i).minutes]
    assert not any(m.sdnn_1 == 0 or m.rmssd == 0 for m in minutes)
    assert sum(1 for m in minutes if m.sdnn_1 is None) == zeros


def test_absent_and_null_are_none_and_never_a_zero():
    minutes = parse.minutes(
        [{"startdate": 0, "enddate": 120, "state": 2, "hr": {"0": 55, "60": 54}, "rr": None}]
    )
    assert [(m.hr, m.rr, m.mvt_score) for m in minutes] == [(55, None, None), (54, None, None)]


# --- keeping them ------------------------------------------------------------------


def test_every_night_is_kept_with_its_stages_and_minutes(db):
    for i in range(len(NIGHTS)):
        night = parsed(i)
        db.save_sleep_night(night)
        assert db.sleep_stages(night.id) == list(night.stages)
        assert db.sleep_minutes(night.id) == list(night.minutes)
        assert db.sleep_night_modified(night.id) == night.modified
    assert [n.wake_on for n in db.sleep_nights()] == [n["date"] for n in NIGHTS]
    assert db.latest_sleep_night().wake_on == NIGHTS[-1]["date"]
    assert db.earliest_sleep_wake_on() == NIGHTS[0]["date"]


def test_a_night_that_grows_replaces_itself_even_under_a_new_id(db):
    night = parsed(0)
    db.save_sleep_night(night)
    grown = replace(night, modified=night.modified + 60, data={**night.data, "sleep_score": 1})
    db.save_sleep_night(grown)
    db.save_sleep_night(replace(night, id=night.id + 1, modified=night.modified + 120))
    held = db.sleep_nights()
    assert [n.id for n in held] == [night.id + 1]
    assert db.sleep_minutes(night.id) == []
    assert len(db.sleep_minutes(night.id + 1)) == len(night.minutes)


def test_the_tokens_wait_for_the_disk_and_nothing_else_does(db):
    seen: list[str] = []
    db._db.set_trace_callback(seen.append)
    db.save_withings_tokens(
        access_token="a", refresh_token="r", expires_at=1, scope="user.activity",
        user_id="u", now=0,
    )
    db._db.set_trace_callback(None)
    order = [s.split()[0] + (" " + s.split()[1] if s.startswith("PRAGMA") else "") for s in seen]
    assert order == [
        "PRAGMA synchronous=FULL", "BEGIN", "INSERT", "COMMIT", "PRAGMA synchronous=NORMAL"
    ]
    assert db._db.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_a_saved_pair_clears_needs_reconnect_and_disconnecting_keeps_the_nights(db):
    db.save_withings_tokens(
        access_token="a", refresh_token="r", expires_at=1, scope=None, user_id=None, now=0
    )
    db.set_withings_needs_reconnect(True)
    assert db.withings_account().needs_reconnect
    db.save_withings_tokens(
        access_token="b", refresh_token="s", expires_at=2, scope=None, user_id=None, now=1
    )
    account = db.withings_account()
    assert not account.needs_reconnect and account.connected_at == 0
    db.save_sleep_night(parsed(0))
    db.forget_withings()
    assert db.withings_account() is None
    assert len(db.sleep_nights()) == 1
