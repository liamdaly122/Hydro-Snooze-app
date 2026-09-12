"""The tonight-only endpoints, over real HTTP.

The first route tests in this project, and they exist because of a bug that no
amount of service-level testing would have found. `post_tonight_stage` called
`_guard_temperature` with two of its three arguments. Every request 500'd. The
service underneath was correct and fully tested; the sixteen lines between it and
the network were not tested at all, and that is where the fault was.

Codex's review put it plainly: passing the existing suite does not establish that
the phone can talk to the hardware. This is a start on the gap rather than a fix
for it.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.main import app as real_app
from hydrosnooze.models import Schedule, SleepStage, Stage
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 12, 20, 0)


@pytest.fixture
def client(tmp_path):
    """The real routes, over real HTTP, with a service this test controls.

    Deliberately not inside `with TestClient(...)`: that runs the app's lifespan,
    which builds a Service from the developer's own .env and starts every
    background loop it has. These tests want a known clock, a temporary database
    and no loops, so the service is put in place directly instead.

    A `create_app(settings, clock)` factory would be tidier than reaching into
    app.state, and is worth adding. It is a change to main.py and does not belong
    in the same commit as a feature.
    """
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False
    )
    service.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    )
    real_app.state.service = service
    real_app.state.build = "test"
    yield TestClient(real_app)
    service.db.close()


def temps(body) -> list[int]:
    return [s["temp_c"] for s in body["running"]["stages"]]


def test_a_stage_for_tonight_answers_with_the_night_it_made(client):
    """The one that 500'd. Two of three arguments, and nothing below the route
    knew about it."""
    r = client.post("/api/tonight/stage", json={"stage": "deep", "temp_c": 17})

    assert r.status_code == 200, r.text
    assert temps(r.json()) == [17, 22, 26]
    assert r.json()["stages_changed"] is True


def test_the_saved_routine_is_not_touched(client):
    client.post("/api/tonight/stage", json={"stage": "deep", "temp_c": 17})
    assert [s["temp_c"] for s in client.get("/api/schedule").json()["stages"]] == [19, 22, 26]


def test_a_temperature_past_the_cap_is_refused_in_words(client):
    r = client.post("/api/tonight/stage", json={"stage": "deep", "temp_c": 99})
    assert r.status_code == 422
    assert "safety cap" in r.json()["detail"]


def test_a_stage_is_judged_in_the_night_it_sits_in(client):
    """Inside the 25 to 35 overlap a stage's mode depends on the one before it,
    so the whole sequence is checked rather than the one number.

    40C is the case that shows it. On its own it is outside cooling's 15 to 35
    entirely; as the Wake stage after a REM of 22 it is a warming stage, where the
    range runs to 55, and it is fine. Liam ran exactly that on 10 September.
    """
    r = client.post("/api/tonight/stage", json={"stage": "wake", "temp_c": 40})

    assert r.status_code == 200, r.text
    assert temps(r.json()) == [19, 22, 40]


def test_sleeping_in_moves_the_alarm_and_lengthens_the_night(client):
    before = client.get("/api/tonight").json()["running"]["night_minutes"]

    r = client.post("/api/tonight/shift", json={"bed_minutes": 0, "wake_minutes": 15})

    assert r.status_code == 200, r.text
    assert r.json()["running"]["wake_time"] == "07:45"
    assert r.json()["running"]["night_minutes"] == before + 15
    assert r.json()["running"]["bed_time"] == "22:30", "bedtime does not move with the alarm"


def test_a_nudge_is_one_degree_and_does_not_stack(client):
    assert client.post("/api/tonight/nudge", json={"delta_c": -5}).json()["nudge_c"] == -1
    assert client.post("/api/tonight/nudge", json={"delta_c": -1}).json()["nudge_c"] == -1


def test_a_nudge_does_not_raise_the_banner(client):
    """It is visible where it happens and lapses on its own. A banner for it would
    be the app telling you something you are already looking at."""
    body = client.post("/api/tonight/nudge", json={"delta_c": -1}).json()
    assert body["nudge_c"] == -1
    assert body["changed"] is False


def test_skipping_and_unskipping(client):
    assert client.post("/api/tonight/skip", params={"skip": True}).json()["skip"] is True
    assert client.post("/api/tonight/skip", params={"skip": False}).json()["skip"] is False


def test_clearing_puts_the_night_back(client):
    client.post("/api/tonight/stage", json={"stage": "deep", "temp_c": 17})
    client.post("/api/tonight/shift", json={"bed_minutes": 0, "wake_minutes": 60})

    body = client.delete("/api/tonight").json()

    assert temps(body) == [19, 22, 26]
    assert body["running"]["wake_time"] == "07:30"
    assert body["changed"] is False


def test_keeping_it_is_the_only_one_that_touches_the_routine(client):
    client.post("/api/tonight/stage", json={"stage": "deep", "temp_c": 17})

    kept = client.post("/api/tonight/keep")

    assert kept.status_code == 200, kept.text
    assert [s["temp_c"] for s in kept.json()["stages"]] == [17, 22, 26]
    assert [s["temp_c"] for s in client.get("/api/schedule").json()["stages"]] == [17, 22, 26]
