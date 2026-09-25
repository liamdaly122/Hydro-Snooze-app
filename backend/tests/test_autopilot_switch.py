"""The switch over all of Autopilot.

Off, the bed runs exactly the temperatures set, at the times set: nothing
learned is used, the drift response does nothing, no suggestion is offered, and
tonight goes back to the usual Deep and REM if it was running one.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, time

import pytest

from hydrosnooze import service as service_module
from hydrosnooze import suggest
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.models import Mode, Power, Schedule, Stage
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import Service

EVENING = datetime.combine(date(2026, 9, 24), time(18, 0))


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(EVENING), echo=False)
    svc.schedule = Schedule(days_of_week=list(range(7)))
    monkeypatch.setattr(svc, "_mat_ready", lambda: True)
    monkeypatch.setattr(suggest, "TEST_EVERY", 1)
    yield svc
    svc.db.close()


def off(svc):
    return asyncio.run(svc.set_autopilot(False))


def test_it_starts_on_and_balanced(service):
    state = service.autopilot_state()
    assert state["on"] is True and state["hold"] == "balanced"
    assert [h["name"] for h in state["holds"]] == ["quiet", "balanced", "close"]


def test_a_database_from_before_the_switch_is_on(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "learning_on INTEGER NOT NULL DEFAULT 1, timing_since TEXT)"
    )
    old.execute("INSERT INTO preferences (id, learning_on) VALUES (1, 0)")
    old.commit()
    old.close()
    db = Database(path)
    try:
        assert db.autopilot_on() is True and db.learning_on() is False
    finally:
        db.close()


def test_off_uses_nothing_learned(service, monkeypatch):
    monkeypatch.setattr(service.db, "learned_offset_c", lambda mode, c: -2.1)
    monkeypatch.setattr(service.db, "learned_lead_minutes", lambda mode, c, gap: 91)
    assert service._correction(28, Mode.WARMING).send_c == 30
    assert service._learned_lead(Mode.WARMING, 28, 10.0) == 91

    off(service)
    assert service._correction(28, Mode.WARMING).send_c == 28, "sent exactly as set"
    assert service._learned_lead(Mode.WARMING, 28, 10.0) is None, "estimated head start"
    assert service.db.learning_on() is True, "Learning's own switch is left as it was"


def test_off_stops_the_drift_response(service, monkeypatch):
    applied = []

    async def apply(mode, target_c):
        applied.append(mode)

    monkeypatch.setattr(service, "_apply", apply)
    monkeypatch.setattr(service_module, "quieter_mode", lambda *a, **k: Mode.QUIET)
    monkeypatch.setattr(type(service.probes), "bed_c", property(lambda self: 19.0))
    service._set_state(assumed_mode=Mode.WARMING)
    step = service.schedule.plan_for(date(2026, 9, 25)).steps[1]

    asyncio.run(service._correct_mode(step, Power.ON, EVENING))
    assert applied == [Mode.QUIET], "on, it switches to the quieter mode"

    applied.clear()
    service._mode_changed_at = None
    off(service)
    asyncio.run(service._correct_mode(step, Power.ON, EVENING))
    assert applied == [], "off, the part stays in the mode the schedule gave it"


def test_off_offers_no_suggestion(service):
    assert service.suggestion()["state"] == "ready"
    off(service)
    assert service.suggestion()["state"] == "off"
    with pytest.raises(CommandFailed):
        asyncio.run(service.accept_suggestion())


def test_turning_off_puts_tonights_suggestion_back_and_keeps_the_rest(service):
    asyncio.run(service.accept_suggestion())
    service.shift_tonight(wake_minutes=15)
    assert service.tonight_suggested() is not None

    off(service)
    tonight = service.tonight_state()
    assert tonight is not None and tonight.stages is None, "Deep and REM back to usual"
    assert tonight.wake_time is not None, "the lie-in is somebody's own and stays"
    assert service.tonight_suggested() is None
    said = [e.message for e in service.events.recent() if "Autopilot off" in e.message]
    assert said and "back to your usual Deep and REM" in said[-1]


def test_turning_off_leaves_a_change_made_by_hand_alone(service):
    asyncio.run(service.set_stage_tonight(Stage.DEEP, 15))
    off(service)
    assert service.tonight_state().stages is not None


def test_tonight_says_when_its_change_is_autopilots(service):
    assert service.tonight_suggested() is None
    offered = asyncio.run(service.accept_suggestion())
    said = service.tonight_suggested()
    assert said["test"] == offered["test"]
    part = offered["test"]["part"]
    assert said["temps"][part] == said["usual"][part] + offered["test"]["offset_c"]

    # Changed again by hand, and it is somebody's own change like any other.
    asyncio.run(service.set_stage_tonight(Stage(part), said["usual"][part] + 3))
    assert service.tonight_suggested() is None


def test_turning_it_back_on_brings_the_suggestion_back(service):
    off(service)
    asyncio.run(service.set_autopilot(True))
    assert service.suggestion()["state"] == "ready"
