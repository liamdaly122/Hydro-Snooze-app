"""A nap: the bed to one temperature, held for a while once it gets there, then off.

The length counts from the bed getting there, not from Start, so the tests here
are mostly about that moment: the probe reading the number, the hoses settling,
the plug going idle, or the estimate running out.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from hydrosnooze import nap
from hydrosnooze.adapters.probes import BUTTON_POWER
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.main import app as real_app
from hydrosnooze.models import (
    DRIFT_MINUTES,
    Activity,
    Mode,
    Power,
    Schedule,
    SleepStage,
    Stage,
)
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import Service, SystemOff

#: A Thursday afternoon. The night is 22:30 to 07:30.
AFTERNOON = datetime(2026, 10, 1, 14, 0)


def _schedule() -> Schedule:
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
    )


class Readings:
    """What the probes say, set by each test."""

    bed: float | None = None
    moving: float | None = None


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(AFTERNOON),
        echo=False,
    )
    svc.schedule = _schedule()
    svc.load_tonight()
    svc.readings = Readings()
    monkeypatch.setattr(type(svc.probes), "bed_c", property(lambda self: svc.readings.bed))
    monkeypatch.setattr(type(svc.probes), "moving_c", property(lambda self: svc.readings.moving))
    svc.pushed = []
    svc.notifier.push = lambda title, body, **_: svc.pushed.append((title, body))
    yield svc
    svc.db.close()


def at(svc: Service, when: datetime) -> datetime:
    svc.clock.jump_to(when)
    return when


# --- When the bed has got there ------------------------------------------------------------


def _nap(mode: Mode = Mode.WARMING, temp: int = 30) -> nap.Nap:
    return nap.Nap(temp, 45, mode, AFTERNOON, AFTERNOON + timedelta(minutes=20))


def arrived(n: nap.Nap, minutes: float, **kw) -> str | None:
    base = dict(bed_c=None, moving_c=None, worked=False, activity=None,
                settle_after_s=120, settled_c=0.4)
    base.update(kw)
    return nap.arrived(n, AFTERNOON + timedelta(minutes=minutes), **base)


def test_the_bed_reading_the_number_is_there():
    assert arrived(_nap(), 5, bed_c=29.5) == "bed"
    assert arrived(_nap(), 5, bed_c=29.4) is None
    assert arrived(_nap(Mode.QUIET, 20), 5, bed_c=20.5) == "bed"
    assert arrived(_nap(Mode.QUIET, 20), 5, bed_c=20.6) is None


def test_the_hoses_settling_is_there_even_short_of_the_number():
    """A warming bed settles a couple of degrees under the setting."""
    assert arrived(_nap(), 10, bed_c=28.0, moving_c=0.3, worked=True) == "probes"
    assert arrived(_nap(), 10, bed_c=28.0, moving_c=0.3, worked=False) is None, (
        "a gap that never opened says nothing"
    )
    assert arrived(_nap(), 1, bed_c=28.0, moving_c=0.3, worked=True) is None, "too soon"


def test_no_probes_and_the_plug_going_idle_is_there():
    assert arrived(_nap(), 10, activity=Activity.IDLE) == "plug"
    assert arrived(_nap(), 10, activity=Activity.HEATING) is None


def test_nothing_says_so_and_the_estimate_runs_out():
    n = _nap()
    assert n.give_up_at == AFTERNOON + timedelta(minutes=35)
    assert arrived(n, 34) is None
    assert arrived(n, 35) == "estimate"


# --- A whole nap -----------------------------------------------------------------------------


async def test_a_nap_counts_from_the_bed_getting_there_and_then_switches_off(service):
    unit = service.transmitter.unit
    state = await service.start_nap(30, 45)
    assert unit.powered and unit.mode is Mode.WARMING and unit.target >= 30
    assert state.nap is not None and state.nap.ready_at is None and state.nap.ends_at is None
    assert state.current_stage is Stage.NAP
    assert service.db.nap() == state.nap, "kept through a restart"
    assert service.db.last_nap() == (30, 45)

    # Twenty minutes in, the bed reads the number.
    ready = at(service, AFTERNOON + timedelta(minutes=20))
    service.readings.bed = 29.8
    service._watch_nap(300.0, ready)
    assert service.state.nap.ready_at == ready
    assert service.state.nap.ends_at == ready + timedelta(minutes=45)
    assert service.pushed[-1][0] == "Nap: bed ready"

    # Not before its time.
    await service._tick()
    at(service, ready + timedelta(minutes=44))
    await service._tick()
    assert unit.powered and service.state.nap is not None

    at(service, ready + timedelta(minutes=45))
    await service._tick()
    assert not unit.powered
    assert service.state.nap is None and service.db.nap() is None
    assert service.state.power is Power.OFF
    assert service.pushed[-1][0] == "Nap over"
    assert not [e for e in service.events.recent(40) if e.level == "error"]


async def test_nothing_confirms_it_and_the_estimate_starts_the_clock(service):
    await service.start_nap(30, 30)
    expect = service.state.nap.give_up_at
    at(service, expect - timedelta(minutes=1))
    service._watch_nap(None, service.clock.now())
    assert service.state.nap.ready_at is None
    noticed = at(service, expect + timedelta(seconds=20))
    service._watch_nap(None, noticed)
    assert service.state.nap.ready_by == "estimate"
    assert service.state.nap.ready_at == expect, "counted from when it ran out"
    assert "could confirm" in service.pushed[-1][1]


async def test_a_nap_long_over_is_forgotten_at_a_restart_not_acted_on(service, tmp_path):
    await service.start_nap(30, 30)
    ready = at(service, AFTERNOON + timedelta(minutes=10))
    service.readings.bed = 30.0
    service._watch_nap(300.0, ready)
    again = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(ready + timedelta(hours=3)),
        echo=False,
    )
    try:
        assert again.state.nap is None and again.db.nap() is None
        before = again.transmitter.presses_sent
        await again._tick()
        assert again.transmitter.presses_sent == before
    finally:
        again.db.close()


async def test_it_comes_back_after_a_restart_and_still_switches_off(service, tmp_path):
    await service.start_nap(30, 30)
    ready = at(service, AFTERNOON + timedelta(minutes=10))
    service.readings.bed = 30.0
    service._watch_nap(300.0, ready)
    again = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(ready + timedelta(minutes=31)),
        echo=False,
    )
    try:
        assert again.state.nap is not None and again.state.nap.ready_at == ready
        again.transmitter.unit.powered = True
        await again._tick()
        assert again.state.nap is None and not again.transmitter.unit.powered
    finally:
        again.db.close()


# --- Stopping ----------------------------------------------------------------------------------


async def test_stop_switches_off_now(service):
    await service.start_nap(30, 30)
    await service.stop_nap()
    assert service.state.nap is None and not service.transmitter.unit.powered


async def test_the_bedside_on_off_ends_it(service):
    await service.start_nap(30, 30)
    service._button_pressed(BUTTON_POWER)
    await service._button_task
    assert service.state.nap is None


async def test_switched_off_some_other_way_ends_it(service):
    await service.start_nap(30, 30)
    later = at(service, AFTERNOON + nap.PLUG_LAG)
    service._watch_nap(0.4, later)
    assert service.state.nap is None


async def test_the_plug_is_not_believed_straight_after_the_presses(service):
    await service.start_nap(30, 30)
    service._watch_nap(0.4, AFTERNOON + timedelta(seconds=30))
    assert service.state.nap is not None


async def test_switching_everything_off_forgets_it_and_sends_nothing(service):
    await service.start_nap(30, 30)
    before = service.transmitter.presses_sent
    await service.set_system(False)
    assert service.state.nap is None
    assert service.transmitter.presses_sent == before


# --- Around the night ----------------------------------------------------------------------------


async def test_tonight_starting_takes_over_without_switching_off(service):
    at(service, datetime(2026, 10, 1, 21, 0))
    await service.start_nap(30, 90)
    service.readings.bed = 30.0
    service._watch_nap(300.0, service.clock.now())
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    preview = service.nap_preview(30, 90)
    assert preview["blocked"] == "A nap is already running."
    at(service, plan.starts_at)
    await service._tick()
    assert service.state.nap is None
    assert service.transmitter.unit.powered, "the night runs on from here"


def test_the_preview_says_when_tonight_would_cut_it_short(service):
    at(service, datetime(2026, 10, 1, 21, 0))
    preview = service.nap_preview(30, 90)
    assert preview["cut_at"] is not None
    at(service, AFTERNOON)
    assert service.nap_preview(30, 45)["cut_at"] is None


async def test_not_inside_a_night(service):
    at(service, datetime(2026, 10, 2, 1, 0))
    with pytest.raises(CommandFailed):
        await service.start_nap(30, 30)


async def test_not_while_switched_off(service):
    await service.set_system(False)
    with pytest.raises(SystemOff):
        await service.start_nap(30, 30)


async def test_one_at_a_time(service):
    await service.start_nap(30, 30)
    with pytest.raises(CommandFailed):
        await service.start_nap(28, 30)


# --- Over HTTP ---------------------------------------------------------------------------------


@pytest.fixture
def client(service):
    real_app.state.service = service
    real_app.state.build = "test"
    return TestClient(real_app)


def test_a_nap_over_http(client):
    preview = client.get("/api/nap").json()
    assert (preview["temp_c"], preview["minutes"]) == (30, 30) and preview["blocked"] is None
    assert preview["mode"] == "warming" and preview["ready_in_minutes"] > 0

    started = client.post("/api/nap", json={"temp_c": 31, "minutes": 45}).json()
    assert started["nap"]["temp_c"] == 31 and started["nap"]["ends_at"] is None
    assert started["current_stage"] == "nap"
    assert client.post("/api/nap", json={"temp_c": 31, "minutes": 45}).status_code == 409

    stopped = client.delete("/api/nap").json()
    assert stopped["nap"] is None
    assert client.get("/api/nap").json()["temp_c"] == 31, "starts from the last one"


def test_nonsense_naps_are_refused(client):
    assert client.post("/api/nap", json={"temp_c": 30, "minutes": 7}).status_code == 422
    assert client.post("/api/nap", json={"temp_c": 30, "minutes": 240}).status_code == 422
    assert client.post("/api/nap", json={"temp_c": 70, "minutes": 30}).status_code == 422
