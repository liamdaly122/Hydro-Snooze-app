"""The switch over everything: off, nothing runs and nothing is sent.

Not a pause and not a holiday. Off, the unit is taken to be switched off or
unplugged, so nothing asks the plug, the blaster or the probe board anything,
no night is planned, and every way of sending something refuses, the bedside
buttons included. On is a fresh start, and a night that was under way picks up
at the part it has reached without ringing through the parts it missed.
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
    Health,
    Mode,
    Power,
    Schedule,
    SleepStage,
    Stage,
)
from hydrosnooze.service import SYSTEM_OFF, Service, SystemOff

#: A Thursday evening, before the night.
EVENING = datetime(2026, 10, 1, 20, 0)
#: Four in the morning of the same night, in REM.
FOUR_AM = datetime(2026, 10, 2, 4, 0)


def _schedule() -> Schedule:
    return Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 27),
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 30),
            SleepStage(Stage.WAKE, 60, 32),
        ],
    )


@pytest.fixture
def service(tmp_path):
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(EVENING),
        echo=False,
    )
    svc.schedule = _schedule()
    svc.load_tonight()
    yield svc
    svc.db.close()


def presses(svc: Service) -> int:
    return svc.transmitter.presses_sent


# --- On, as it has always been ------------------------------------------------------


def test_it_starts_on(service):
    assert service.db.system_on() is True
    assert service.state.system_on is True


def test_a_database_from_before_the_switch_is_on(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "learning_on INTEGER NOT NULL DEFAULT 1, timing_since TEXT)"
    )
    old.execute("INSERT INTO preferences (id) VALUES (1)")
    old.commit()
    old.close()
    db = Database(path)
    try:
        assert db.system_on() is True
    finally:
        db.close()


# --- Off -----------------------------------------------------------------------------


async def test_off_the_unit_is_taken_to_be_off_and_no_night_is_planned(service):
    state = await service.set_system(False)
    assert state.system_on is False
    assert state.power is Power.OFF and state.current_stage is None
    for at in (EVENING, datetime(2026, 10, 1, 22, 31), FOUR_AM):
        assert service.scheduler.plan_in_progress(service.schedule, at) is None
        assert service.scheduler.due(service.schedule, at) is None
        assert service.scheduler.stage_now(service.schedule, at) is None


async def test_off_a_whole_night_goes_by_with_nothing_sent_and_nothing_said(service):
    await service.set_system(False)
    before = presses(service)
    pushed: list[str] = []
    service.notifier.push = lambda title, body, **_: pushed.append(title)
    marked = len(service.events.recent(500))

    at = EVENING
    while at < datetime(2026, 10, 2, 12, 0):
        service.clock.jump_to(at)
        await service._tick()
        await service._sample_power()
        at += timedelta(minutes=5)

    assert presses(service) == before
    said = service.events.recent(500)[: len(service.events.recent(500)) - marked]
    assert said == [], [e.message for e in said]
    assert pushed == []


async def test_off_nothing_asks_the_plug(service, monkeypatch):
    await service.set_system(False)
    asked: list[int] = []

    async def read():
        asked.append(1)
        return 0.0

    monkeypatch.setattr(service.power, "read_watts", read)
    await service._sample_power()
    assert asked == []


async def test_off_every_way_of_sending_something_refuses(service):
    await service.set_system(False)
    before = presses(service)
    for send in (
        service.power_on,
        service.power_off,
        service.press_power,
        service.mute,
        service.reboot_blaster,
        lambda: service.set_temperature(24),
        lambda: service.set_mode(Mode.QUIET),
        lambda: service.start_rehearsal(120),
    ):
        with pytest.raises(SystemOff):
            await send()
    assert presses(service) == before


async def test_off_a_bedside_press_is_said_and_sends_nothing(service):
    await service.set_system(False)
    service.clock.jump_to(FOUR_AM)
    before = presses(service)
    service._button_pressed(BUTTON_COOLER)
    await service._button_task
    assert presses(service) == before
    said = [e.message for e in service.events.recent(10) if e.kind == "buttons"]
    assert said[0] == "Bedside: 1 cooler. HydroSnooze is switched off, so nothing was sent."


async def test_off_tonights_changes_are_kept_and_not_sent(service):
    """Settings are data, and can still be changed. Nothing follows them."""
    await service.set_system(False)
    service.clock.jump_to(FOUR_AM)
    before = presses(service)
    await service.set_stage_tonight(Stage.REM, 28)
    assert service.tonight_now().stage(Stage.REM).temp_c == 28
    assert presses(service) == before


async def test_off_the_devices_are_grey_not_red(service):
    await service.set_system(False)
    by = {d.name: d.health for d in service.health()}
    assert by["plug"] is by["blaster"] is by["probes"] is Health.OFF
    assert by["alerts"] is not Health.OFF, "whether anything is watching still matters"


async def test_off_stops_a_rehearsal_without_touching_the_unit(service):
    await service.start_rehearsal(120)
    assert service.scheduler.rehearsal is not None
    before = presses(service)
    await service.set_system(False)
    assert service.scheduler.rehearsal is None
    assert presses(service) == before


async def test_off_is_still_off_after_a_restart(service, tmp_path):
    await service.set_system(False)
    again = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(FOUR_AM),
        echo=False,
    )
    try:
        assert again.state.system_on is False and again.state.power is Power.OFF
    finally:
        again.db.close()


# --- On again ------------------------------------------------------------------------


async def test_on_mid_night_picks_up_the_part_it_has_reached_without_ringing(service):
    """Off before bed, on at four. Drift and Deep went by with it off, which is
    what was asked for, not a failure: no error, no push. REM, which is running,
    runs."""
    await service.set_system(False)
    service.clock.jump_to(FOUR_AM)
    pushed: list[str] = []
    service.notifier.push = lambda title, body, **_: pushed.append(title)

    state = await service.set_system(True)
    assert state.system_on is True
    assert service._resume_task is not None, "REM started an hour ago: past the tick's window"
    await service._resume_task
    await service._tick()

    recent = service.events.recent(40)
    assert not [e for e in recent if e.level == "error"], [e.message for e in recent]
    assert pushed == []
    unit = service.transmitter.unit
    assert unit.powered and unit.target == 30, "REM, from where the night has got to"
    plan = service.schedule.plan_for(date(2026, 10, 2))
    assert "stage:rem" in service.scheduler.fired.keys_for(plan)


async def test_on_the_night_runs_again(service):
    await service.set_system(False)
    await service.set_system(True)
    service.clock.jump_to(datetime(2026, 10, 1, 22, 31))
    assert service.scheduler.stage_now(service.schedule, service.clock.now()) is not None


async def test_flipping_to_where_it_already_is_changes_nothing(service):
    marked = len(service.events.recent(500))
    await service.set_system(True)
    assert len(service.events.recent(500)) == marked


# --- Over HTTP ------------------------------------------------------------------------


@pytest.fixture
def client(service):
    real_app.state.service = service
    real_app.state.build = "test"
    return TestClient(real_app)


def test_the_switch_over_http(client):
    assert client.get("/api/state").json()["system_on"] is True
    off = client.post("/api/system", json={"on": False}).json()
    assert off["system_on"] is False and off["power"] == "off"
    refused = client.post("/api/power/on")
    assert refused.status_code == 409 and refused.json()["detail"] == SYSTEM_OFF
    assert client.post("/api/temperature", json={"target_c": 24}).status_code == 409
    assert client.post("/api/system", json={"on": True}).json()["system_on"] is True
