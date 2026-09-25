"""The weekend's own times, and how a longer night is laid out.

Two things are being held here. The first is plain: a morning in other_days
gets other_bed_time and other_wake_time, everywhere a night is planned. The
second is Autopilot's: with it on, a night longer or shorter than the usual one
keeps Drift, Deep and Wake as long as they usually are and gives REM the
difference, because deep sleep does not move with the alarm. With it off, the
night stretches evenly, exactly as set.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.main import app as real_app
from hydrosnooze.models import (
    MIN_STAGE_MINUTES,
    Schedule,
    SleepStage,
    Stage,
    Tonight,
    laid_out_like,
)
from hydrosnooze.scheduler import Scheduler
from hydrosnooze.service import Service

FRIDAY = date(2026, 9, 25)
SATURDAY = FRIDAY + timedelta(days=1)
SAT, SUN = 5, 6


def usual(**kw) -> Schedule:
    """22:30 to 06:30 every morning, and 23:30 to 08:30 on Saturday and Sunday."""
    return Schedule(
        days_of_week=list(range(7)),
        bed_time=time(22, 30),
        wake_time=time(6, 30),
        other_days=[SAT, SUN],
        other_bed_time=time(23, 30),
        other_wake_time=time(8, 30),
        **kw,
    )


def lengths(schedule: Schedule) -> dict[Stage, int]:
    return {s.stage: s.duration_minutes for s in schedule.stages}


# --- Which times a morning gets ----------------------------------------------------------


def test_the_other_days_get_the_other_times():
    schedule = usual()
    assert schedule.times_for(SATURDAY) == (time(23, 30), time(8, 30))
    assert schedule.times_for(FRIDAY) == (time(22, 30), time(6, 30))
    assert schedule.for_morning(FRIDAY) is schedule


def test_half_set_other_times_are_no_other_times():
    """Days chosen and no times yet, or times and no days: every night is usual."""
    no_times = Schedule(other_days=[SAT], other_bed_time=None, other_wake_time=time(8, 30))
    assert no_times.times_for(SATURDAY) == (no_times.bed_time, no_times.wake_time)
    no_days = Schedule(other_days=[], other_bed_time=time(23, 0), other_wake_time=time(8, 0))
    assert no_days.times_for(SATURDAY) == (no_days.bed_time, no_days.wake_time)


def test_a_saturday_is_planned_on_saturdays_times():
    scheduler = Scheduler()
    plan = scheduler.shape(usual(), SATURDAY).plan_for(SATURDAY)
    assert plan.wake_at == datetime(2026, 9, 26, 8, 30)
    assert plan.bedtime_at == datetime(2026, 9, 25, 23, 30)

    # And Friday's night, the one before it, is a weekday night.
    plan = scheduler.shape(usual(), FRIDAY).plan_for(FRIDAY)
    assert plan.wake_at == datetime(2026, 9, 25, 6, 30)


def test_the_scheduler_runs_the_saturday_it_planned():
    """plan_in_progress is what actually drives the bed."""
    scheduler = Scheduler()
    plan = scheduler.plan_in_progress(usual(), datetime(2026, 9, 25, 20, 0))
    assert plan is not None
    assert plan.wake_at == datetime(2026, 9, 26, 8, 30)


def test_next_plan_finds_the_weekend_times_too():
    plan = usual().next_plan(datetime(2026, 9, 25, 20, 0))
    assert plan is not None and plan.wake_at == datetime(2026, 9, 26, 8, 30)


# --- How a longer night is laid out ---------------------------------------------------------


def test_with_autopilot_on_rem_takes_the_extra_time():
    schedule = usual()
    scheduler = Scheduler(autopilot_on=lambda: True)
    weekday, saturday = lengths(schedule), lengths(scheduler.shape(schedule, SATURDAY))

    for kept in (Stage.DRIFT, Stage.DEEP, Stage.WAKE):
        assert saturday[kept] == weekday[kept], kept
    # An hour more night, all of it REM.
    assert saturday[Stage.REM] == weekday[Stage.REM] + 60
    assert sum(saturday.values()) == 540


def test_with_autopilot_off_the_night_stretches_evenly_as_set():
    schedule = usual()
    scheduler = Scheduler(autopilot_on=lambda: False)
    weekday, saturday = lengths(schedule), lengths(scheduler.shape(schedule, SATURDAY))

    assert saturday[Stage.DRIFT] == weekday[Stage.DRIFT]
    assert saturday[Stage.DEEP] > weekday[Stage.DEEP]
    assert saturday[Stage.REM] > weekday[Stage.REM]
    assert sum(saturday.values()) == 540


def test_a_usual_length_night_is_left_exactly_alone():
    schedule = usual()
    assert laid_out_like(schedule, schedule) is schedule


def test_a_night_too_short_for_rem_falls_back_to_the_even_stretch():
    """Keeping Deep at four hours in a five hour night would squeeze REM to
    nothing. The even stretch is the better answer there."""
    schedule = Schedule(
        days_of_week=list(range(7)),
        bed_time=time(22, 30),
        wake_time=time(6, 30),
        other_days=[SAT],
        other_bed_time=time(2, 0),
        other_wake_time=time(7, 0),
        stages=[
            SleepStage(Stage.DRIFT, 35, 18),
            SleepStage(Stage.DEEP, 300, 17),
            SleepStage(Stage.REM, 115, 20),
            SleepStage(Stage.WAKE, 30, 26),
        ],
    )
    night = Scheduler(autopilot_on=lambda: True).shape(schedule, SATURDAY)
    assert sum(lengths(night).values()) == 300
    assert all(m >= MIN_STAGE_MINUTES for m in lengths(night).values())
    assert lengths(night)[Stage.DEEP] < 300


def test_sleeping_in_on_a_saturday_is_past_saturdays_alarm():
    """Tonight's exceptions sit over the day's own times, and Autopilot lays out
    the result like any other longer night."""
    schedule = usual()
    tonight = Tonight(wake_on=SATURDAY, wake_time=time(9, 30))
    scheduler = Scheduler(tonight=tonight, autopilot_on=lambda: True)
    night = scheduler.shape(schedule, SATURDAY)

    assert night.bed_time == time(23, 30)
    assert night.wake_time == time(9, 30)
    assert lengths(night)[Stage.DEEP] == lengths(schedule)[Stage.DEEP]
    assert lengths(night)[Stage.REM] == lengths(schedule)[Stage.REM] + 120


def test_sleeping_in_through_the_service_starts_from_the_days_own_alarm(tmp_path):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime(2026, 9, 25, 20, 0)),
        echo=False,
    )
    service.schedule = usual()
    try:
        service.shift_tonight(wake_minutes=60)
        assert service.tonight_now().wake_time == time(9, 30)
    finally:
        service.db.close()


# --- Kept, and sent ---------------------------------------------------------------------------


def test_the_other_times_survive_a_restart(tmp_path):
    db = Database(tmp_path / "s.db")
    db.save_schedule(usual())
    back = db.load_schedule()
    assert back.other_days == [SAT, SUN]
    assert back.other_bed_time == time(23, 30)
    assert back.other_wake_time == time(8, 30)

    db.save_schedule(Schedule())
    cleared = db.load_schedule()
    assert cleared.other_days == [] and cleared.other_bed_time is None
    db.close()


@pytest.fixture
def client(tmp_path):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime(2026, 9, 25, 12, 0)),
        echo=False,
    )
    real_app.state.service = service
    real_app.state.build = "test"
    yield TestClient(real_app)
    service.db.close()


def test_setting_the_other_times_over_http(client):
    r = client.put(
        "/api/schedule",
        json={"other_days": [6, 5, 5], "other_bed_time": "23:30", "other_wake_time": "08:30"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["other_days"] == [5, 6]
    assert body["other_bed_time"] == "23:30" and body["other_wake_time"] == "08:30"
    assert body["other_night_minutes"] == 540

    r = client.put("/api/schedule", json={"other_bed_time": "", "other_wake_time": ""})
    assert r.status_code == 200, r.text
    assert r.json()["other_bed_time"] is None and r.json()["other_night_minutes"] is None


def test_an_other_night_too_short_for_its_stages_is_refused(client):
    r = client.put(
        "/api/schedule",
        json={"other_days": [5], "other_bed_time": "06:00", "other_wake_time": "06:30"},
    )
    assert r.status_code == 422
    assert "other days" in r.json()["detail"]


def test_other_days_must_be_days(client):
    r = client.put("/api/schedule", json={"other_days": [7]})
    assert r.status_code == 422


# --- Sleep timing ------------------------------------------------------------------------------


def test_sleep_timing_measures_a_weekend_from_its_own_lights_out(tmp_path):
    """The same sleep, an hour later on Saturday and Sunday. Measured from the
    weekday's lights out, the weekend nights would read an hour late to fall
    asleep, and the middle of the nights would no longer agree. From their own
    lights out they read exactly like every other night."""
    from test_sleep_timing import DEEP_DONE_MIN, TYPICAL, night_of

    from hydrosnooze.withings import timing

    db = Database(tmp_path / "t.db")
    schedule = usual()
    for back in range(21):
        morning = FRIDAY - timedelta(days=back)
        starts = time(23, 30) if morning.weekday() in (SAT, SUN) else time(22, 30)
        db.save_sleep_night(night_of(morning, TYPICAL, starts=starts))

    built = timing.timing(db, schedule, FRIDAY)
    drift = next(b for b in built["boundaries"] if b["part"] == "drift")
    deep = next(b for b in built["boundaries"] if b["part"] == "deep")
    assert drift["measured"] == {"median_min": 20, "low_min": 20, "high_min": 20}
    assert deep["measured"]["median_min"] == DEEP_DONE_MIN
    assert deep["measured"]["low_min"] == deep["measured"]["high_min"] == DEEP_DONE_MIN
    db.close()


def test_tonight_says_what_this_night_usually_is(tmp_path):
    """Friday evening, heading for Saturday morning. Its alarm is Saturday's
    08:30, and that is its usual, not a one-off change from 06:30."""
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime(2026, 9, 25, 20, 0)),
        echo=False,
    )
    service.schedule = usual()
    real_app.state.service = service
    real_app.state.build = "test"
    try:
        body = TestClient(real_app).get("/api/tonight").json()
        assert body["running"]["wake_time"] == "08:30"
        assert body["usual_wake_time"] == "08:30"
        assert body["usual_bed_time"] == "23:30"
        assert body["times_changed"] is False
    finally:
        service.db.close()
