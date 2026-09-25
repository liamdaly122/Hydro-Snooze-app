"""How a night felt, and what it does to the scoreboard. See notes.py.

The scoreboard nights here are built the same way test_scoreboard builds them,
by hand, so every average can be checked on paper.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from test_scoreboard import NOON, SWING, TODAY, mat, run

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.main import app as real_app
from hydrosnooze.notes import NightNote
from hydrosnooze.service import Service
from hydrosnooze.trials import NightRun, PartRun
from hydrosnooze.withings import scoreboard


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "n.db")
    yield database
    database.close()


def part(built, name):
    return next(p for p in built["parts"] if p["part"] == name)


def with_wake(r: NightRun, wake_c: int) -> NightRun:
    """The same night with Wake at another setting."""
    parts = tuple(
        PartRun(p.part, p.starts_at, p.ends_at, wake_c, p.held, p.bed_c, p.by_hand)
        if p.part == "wake"
        else p
        for p in r.parts
    )
    return NightRun(r.wake_on, r.bedtime_at, r.wake_at, parts, r.room_c)


def night(db, days_ago: int, *, note: NightNote | None = None, wake_c: int = 26, **mat_kw):
    morning = TODAY - timedelta(days=days_ago)
    db.save_night_run(with_wake(run(morning), wake_c), NOON)
    db.save_sleep_night(mat(morning, **mat_kw))
    if note is not None:
        db.save_night_note(
            NightNote(morning.isoformat(), note.rating, note.felt, note.tags), NOON
        )


# --- Kept -------------------------------------------------------------------------------------


def test_a_note_is_kept_and_read_back(db):
    db.save_night_note(NightNote("2026-09-25", 4, "too_warm", ("alcohol", "exercise")), NOON)
    back = db.night_note("2026-09-25")
    assert back == NightNote("2026-09-25", 4, "too_warm", ("alcohol", "exercise"))
    assert back.left_out_by() == ["Alcohol"]
    assert db.night_note("2026-09-24") is None


def test_the_three_that_leave_a_night_out_are_the_three_asked_for():
    note = NightNote("x", tags=("company", "ill", "alcohol", "caffeine", "late_meal", "exercise", "stressed"))
    assert note.left_out_by() == ["Alcohol", "Ill", "Someone else in the bed"]


# --- The scoreboard -------------------------------------------------------------------------


def test_a_tagged_night_is_left_out_of_the_scoreboard_and_said(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, deep_m=90 + d)
    # Two nights that would drag the average down, tagged for why.
    night(db, 11, deep_m=10, note=NightNote("", tags=("alcohol",)))
    night(db, 12, deep_m=10, note=NightNote("", tags=("company", "ill")))

    built = scoreboard.scoreboard(db, TODAY)
    assert built["nights"] == 10
    assert built["left_out"] == {
        "nights": 2,
        "by_tag": {"Alcohol": 1, "Someone else in the bed": 1, "Ill": 1},
    }
    [only] = part(built, "deep")["settings"]
    assert only["nights"] == 10
    assert only["mean_s"] == (90 + 100) * 60


def test_a_tag_that_does_not_leave_out_changes_nothing(db):
    for i, d in enumerate(SWING):
        night(db, i + 1, deep_m=90 + d, note=NightNote("", tags=("exercise",)))
    built = scoreboard.scoreboard(db, TODAY)
    assert built["nights"] == 10 and built["left_out"]["nights"] == 0


def test_wake_is_scored_on_the_mornings_rating(db):
    """Two Wake settings, five mornings each, rated well apart."""
    for i in range(5):
        night(db, i + 1, wake_c=26, note=NightNote("", rating=[2, 3, 2, 3, 2][i]))
    for i in range(5):
        night(db, i + 6, wake_c=28, note=NightNote("", rating=[4, 5, 4, 5, 4][i]))
    # A morning nobody answered does not count for Wake, and counts for the rest.
    night(db, 11, wake_c=28)

    built = scoreboard.scoreboard(db, TODAY)
    wake = part(built, "wake")
    assert wake["unit"] == "rating"
    by = {s["set_c"]: s for s in wake["settings"]}
    assert by[26]["nights"] == 5 and by[26]["mean_s"] == 2.4
    assert by[28]["nights"] == 5 and by[28]["mean_s"] == 4.4
    assert wake["verdict"] == "clear" and wake["leader_c"] == 28
    assert wake["gap_s"] == 2.0
    assert part(built, "deep")["unit"] == "seconds"
    assert built["nights"] == 11


def test_ratings_too_close_to_call_are_not_called(db):
    for i in range(5):
        night(db, i + 1, wake_c=26, note=NightNote("", rating=[3, 4, 3, 4, 3][i]))
    for i in range(5):
        night(db, i + 6, wake_c=28, note=NightNote("", rating=[4, 3, 4, 3, 4][i]))
    assert part(scoreboard.scoreboard(db, TODAY), "wake")["verdict"] == "not_sure"


def test_each_setting_says_how_the_bed_felt_on_its_nights(db):
    felt = ["too_warm", "too_warm", "right", None, "too_cold"]
    for i, f in enumerate(felt):
        night(db, i + 1, note=NightNote("", felt=f))
    [deep] = part(scoreboard.scoreboard(db, TODAY), "deep")["settings"]
    assert deep["felt"] == {"answered": 4, "too_warm": 2, "too_cold": 1}


def test_a_tagged_test_night_says_it_does_not_count(db):
    morning = TODAY - timedelta(days=1)
    r = run(morning, deep=16)
    db.save_night_run(NightRun(r.wake_on, r.bedtime_at, r.wake_at, r.parts, r.room_c, "deep", -1), NOON)
    db.save_sleep_night(mat(morning))
    db.save_night_note(NightNote(morning.isoformat(), tags=("alcohol",)), NOON)

    result = scoreboard.test_result(db, morning.isoformat(), TODAY)
    assert result is not None
    assert result["counted"] is False
    assert result["left_out"] == ["Alcohol"]


# --- Over HTTP ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime.combine(TODAY, time(7, 30))),
        echo=False,
    )
    real_app.state.service = service
    real_app.state.build = "test"
    yield TestClient(real_app)
    service.db.close()


def test_a_morning_with_nothing_said_answers_with_the_choices(client):
    got = client.get("/api/notes", params={"date": "2026-09-25"}).json()
    assert got["rating"] is None and got["felt"] is None and got["tags"] == []
    assert [c["label"] for c in got["choices"]["ratings"]] == ["Rough", "Groggy", "OK", "Good", "Great"]
    leaves = [t["label"] for t in got["choices"]["tags"] if t["leaves_out"]]
    assert leaves == ["Alcohol", "Ill", "Someone else in the bed"]


def test_only_what_is_sent_changes(client):
    url = "/api/notes/2026-09-25"
    assert client.put(url, json={"rating": 4}).json()["rating"] == 4
    got = client.put(url, json={"tags": ["exercise", "alcohol", "alcohol"]}).json()
    assert got["rating"] == 4
    assert got["tags"] == ["alcohol", "exercise"]
    assert got["left_out"] == ["Alcohol"]
    got = client.put(url, json={"felt": "too_warm", "rating": None}).json()
    assert got["rating"] is None and got["felt"] == "too_warm" and got["tags"] == ["alcohol", "exercise"]


def test_nonsense_is_refused(client):
    url = "/api/notes/2026-09-25"
    assert client.put(url, json={"rating": 6}).status_code == 422
    assert client.put(url, json={"felt": "lukewarm"}).status_code == 422
    assert client.put(url, json={"tags": ["hungover"]}).status_code == 422
    assert client.put("/api/notes/not-a-date", json={"rating": 3}).status_code == 422


def test_tonight_can_be_tagged_but_not_next_week(client):
    """The coming morning, for a tag known in the evening. Not beyond."""
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    later = (TODAY + timedelta(days=2)).isoformat()
    assert client.put(f"/api/notes/{tomorrow}", json={"tags": ["alcohol"]}).status_code == 200
    assert client.put(f"/api/notes/{later}", json={"tags": ["alcohol"]}).status_code == 422
