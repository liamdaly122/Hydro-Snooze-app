"""Bedtime only: ready by lights out, one temperature for a while, then off.

A setting, not tonight's: it stays until it is switched back, and the four
parts of the whole night are left exactly as they were underneath it. The
night keeps its wake time, because that is what it is called by and when the
morning report goes; only the switch-off moves, to the end of the one part.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.adapters.probes import BUTTON_COOLER
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.main import app as real_app
from hydrosnooze.models import (
    DRIFT_MINUTES,
    STAGE_ORDER,
    Mode,
    Power,
    Schedule,
    SleepStage,
    Stage,
    plan_for_bedtime,
)
from hydrosnooze.scheduler import REPORT_AFTER
from hydrosnooze.service import Service

#: Thursday evening. The night is Thursday to Friday 2 October.
EVENING = datetime(2026, 10, 1, 20, 0)
FRIDAY = date(2026, 10, 2)


def _schedule(**over) -> Schedule:
    return Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 27),
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 26),
            SleepStage(Stage.WAKE, 60, 28),
        ],
        **over,
    )


BEDTIME = dict(bedtime_only=True, bedtime_temp_c=30, bedtime_minutes=90)


@pytest.fixture
def service(tmp_path):
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(EVENING),
        echo=False,
    )
    svc.schedule = _schedule(**BEDTIME)
    svc.load_tonight()
    yield svc
    svc.db.close()


# --- The plan -----------------------------------------------------------------------


def test_one_part_from_lights_out_then_off_and_the_morning_stays_the_morning():
    plan = _schedule(**BEDTIME).plan_for(FRIDAY)
    [step] = plan.steps
    assert step.stage is Stage.BEDTIME and step.temp_c == 30 and step.mode is Mode.WARMING
    assert step.starts_at == datetime(2026, 10, 1, 22, 30)
    assert step.ends_at == datetime(2026, 10, 2, 0, 0)
    assert plan.switch_off_at == datetime(2026, 10, 2, 0, 0)
    assert plan.wake_at == datetime(2026, 10, 2, 7, 30), "the night is still Friday's"
    assert plan.precool_at is not None and plan.precool_at < plan.bedtime_at
    assert plan.preconditioning.mode is Mode.WARMING


def test_a_cool_number_cools():
    plan = _schedule(bedtime_only=True, bedtime_temp_c=21, bedtime_minutes=120).plan_for(FRIDAY)
    assert plan.steps[0].mode.is_cooling


def test_never_past_the_alarm():
    plan = plan_for_bedtime(FRIDAY, time(22, 30), time(7, 30), 30, 720)
    assert plan.steps[0].ends_at == plan.wake_at
    assert plan.off_at is None and plan.switch_off_at == plan.wake_at


def test_the_whole_night_is_kept_underneath():
    on = _schedule(**BEDTIME)
    assert [s.stage for s in on.stages] == list(STAGE_ORDER)
    assert on.stage(Stage.DEEP).temp_c == 19
    off = _schedule()
    assert [s.stage for s in off.plan_for(FRIDAY).steps] == list(STAGE_ORDER)


def test_off_is_the_whole_night_as_it_always_was():
    assert _schedule().plan_for(FRIDAY).off_at is None


# --- The night, through the scheduler -------------------------------------------------


def test_switched_off_at_the_end_of_the_part_and_reported_in_the_morning(service):
    s = service.scheduler
    plan = s.plan_in_progress(service.schedule, EVENING)
    assert plan is not None and plan.off_at is not None
    mid = datetime(2026, 10, 1, 23, 30)
    assert s.stage_now(service.schedule, mid).stage is Stage.BEDTIME
    after = datetime(2026, 10, 2, 0, 1)
    assert s.stage_now(service.schedule, after) is None, "nothing runs once it is off"
    assert s.due(service.schedule, after).kind == "power_off"
    s.fired.mark(s.due(service.schedule, after))
    assert s.due(service.schedule, datetime(2026, 10, 2, 3, 0)) is None
    assert s.due(service.schedule, plan.wake_at + REPORT_AFTER).kind == "report"


async def test_a_whole_night_on_the_simulated_unit(service):
    pushed: list[tuple[str, str]] = []
    service.notifier.push = lambda title, body, **_: pushed.append((title, body))
    unit = service.transmitter.unit

    at = EVENING
    on_at_midnight = None
    while at < datetime(2026, 10, 2, 8, 0):
        service.clock.jump_to(at)
        await service._tick()
        if at == datetime(2026, 10, 1, 23, 30):
            assert unit.powered and unit.target >= 30, "warming at the bedtime number"
        if at == datetime(2026, 10, 2, 0, 30):
            on_at_midnight = unit.powered
        at += timedelta(minutes=1)

    assert on_at_midnight is False, "off for the rest of the night"
    assert not [e for e in service.events.recent(200) if e.level == "error"]
    report = [body for title, body in pushed if title.startswith("Autopilot")]
    assert report and report[0].startswith(
        "Bedtime only: warmed to 30C from 22:30 to 00:00, then off."
    )


# --- Tonight, and the bedside -----------------------------------------------------------


async def test_tonight_only_and_then_kept(service):
    await service.set_stage_tonight(Stage.BEDTIME, 28)
    assert service.tonight_now().bedtime_temp_c == 28
    assert service.schedule.bedtime_temp_c == 30, "the usual is untouched"
    assert service.tonight_state().anything_to_say()

    service.keep_tonight()
    assert service.schedule.bedtime_temp_c == 28
    tonight = service.tonight_state()
    assert tonight is None or tonight.bedtime_temp_c is None


async def test_the_bedside_moves_the_bedtime_part(service):
    service.clock.jump_to(datetime(2026, 10, 1, 23, 0))
    service._set_state(power=Power.ON)
    service._button_pressed(BUTTON_COOLER)
    await service._button_task
    said = [e.message for e in service.events.recent(10) if e.kind == "buttons"]
    assert said[0] == "Bedside: 1 cooler. Bedtime goes to 29C."
    assert service.tonight_now().bedtime_temp_c == 29


def test_autopilot_has_nothing_to_choose(service):
    assert service.suggestion()["state"] == "bedtime_only"


async def test_a_rehearsal_is_the_one_part(service):
    plan = await service.start_rehearsal(120)
    assert [s.stage for s in plan.steps] == [Stage.BEDTIME]
    await service.stop_rehearsal(power_off=False)


# --- Kept ------------------------------------------------------------------------------


def test_saved_and_read_back(tmp_path):
    db = Database(tmp_path / "s.db")
    try:
        db.save_schedule(_schedule(bedtime_only=True, bedtime_temp_c=31, bedtime_minutes=105))
        back = db.load_schedule()
        assert (back.bedtime_only, back.bedtime_temp_c, back.bedtime_minutes) == (True, 31, 105)
    finally:
        db.close()


def test_a_schedule_from_before_it_is_off(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE schedule (id INTEGER PRIMARY KEY CHECK (id = 1), name TEXT NOT NULL, "
        "enabled INTEGER NOT NULL, days_of_week TEXT NOT NULL, wake_time TEXT NOT NULL, "
        "bed_time TEXT NOT NULL, stages TEXT NOT NULL, cooling_speed TEXT NOT NULL, "
        "updated_at TEXT)"
    )
    old.execute(
        "INSERT INTO schedule VALUES (1, 'Tonight', 1, '[0,1,2,3,4]', '06:30', '22:30', "
        "'[]', 'quiet', NULL)"
    )
    old.commit()
    old.close()
    db = Database(path)
    try:
        back = db.load_schedule()
        assert back.bedtime_only is False and back.bedtime_minutes == 90
    finally:
        db.close()


# --- Over HTTP ---------------------------------------------------------------------------


@pytest.fixture
def client(service):
    service.schedule = _schedule()
    real_app.state.service = service
    real_app.state.build = "test"
    return TestClient(real_app)


def test_switched_on_and_set_over_http(client):
    got = client.put(
        "/api/schedule", json={"bedtime_only": True, "bedtime_temp_c": 31, "bedtime_minutes": 75}
    ).json()
    assert got["bedtime_only"] is True and got["bedtime_temp_c"] == 31
    assert got["bedtime_minutes"] == 75 and got["bedtime_mode"] == "warming"
    assert len(got["stages"]) == 4, "the whole night is still there"


def test_nonsense_is_refused(client):
    assert client.put("/api/schedule", json={"bedtime_minutes": 10}).status_code == 422
    assert client.put("/api/schedule", json={"bedtime_minutes": 50}).status_code == 422
    assert client.put("/api/schedule", json={"bedtime_temp_c": 70}).status_code == 422
    bedtime_stage = {"stage": "bedtime", "duration_minutes": 60, "temp_c": 30}
    assert client.put("/api/schedule", json={"stages": [bedtime_stage]}).status_code == 422


def test_no_bedtime_part_tonight_while_it_is_off(client):
    refused = client.post("/api/tonight/stage", json={"stage": "bedtime", "temp_c": 30})
    assert refused.status_code == 422
