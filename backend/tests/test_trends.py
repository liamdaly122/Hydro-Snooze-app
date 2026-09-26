"""Trends: the nights over weeks and months. See trends.py.

Nights built by hand the way test_scoreboard builds them, so every average and
every total can be checked on paper.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from test_scoreboard import NOON, TODAY, mat, run

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.main import app as real_app
from hydrosnooze.notes import NightNote
from hydrosnooze.service import Service
from hydrosnooze.trends import trends
from hydrosnooze.trials import NightRun


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def night(db, days_ago: int, *, kwh: float | None = 1.5, on_mat: bool = True, **mat_kw) -> None:
    morning = TODAY - timedelta(days=days_ago)
    r = run(morning)
    db.save_night_run(
        NightRun(r.wake_on, r.bedtime_at, r.wake_at, r.parts, r.room_c, kwh=kwh), NOON
    )
    if on_mat:
        db.save_sleep_night(mat(morning, **mat_kw))


def test_each_night_is_a_row_with_the_mat_the_bed_and_the_bill(db):
    night(db, 0, deep_m=80, rem_m=100, latency_m=12, kwh=1.25)
    built = trends(db, TODAY, 30, tariff_p=25.0)
    [row] = built["nights"]
    assert row["wake_on"] == TODAY.isoformat()
    assert row["deep_rem_s"] == 180 * 60
    assert row["latency_s"] == 12 * 60
    assert row["kwh"] == 1.25 and row["cost_p"] == 31.2
    assert row["room_c"] == 19.0
    # The bed across the night, each part by how long it ran: 19.5 for thirty
    # minutes, 17.5 for four hours, 20.5 for three, 26.5 for half an hour.
    assert row["bed_c"] == round((19.5 * 30 + 17.5 * 240 + 20.5 * 180 + 26.5 * 30) / 480, 1)


def test_a_night_nothing_measured_is_a_gap_not_a_nought(db):
    night(db, 0, kwh=None, on_mat=False)
    [row] = trends(db, TODAY, 30, tariff_p=25.0)["nights"]
    assert row["kwh"] is None and row["cost_p"] is None
    assert row["score"] is None and row["deep_rem_s"] is None


def test_no_tariff_means_no_cost(db):
    night(db, 0)
    [row] = trends(db, TODAY, 30, tariff_p=None)["nights"]
    assert row["cost_p"] is None
    assert trends(db, TODAY, 30, tariff_p=None)["summary"]["now"]["cost_p_total"] is None


def test_the_stretch_is_set_against_the_one_before(db):
    for i in range(30):
        night(db, i, deep_m=100, kwh=2.0)
    for i in range(30, 60):
        night(db, i, deep_m=80, kwh=1.0)
    built = trends(db, TODAY, 30, tariff_p=20.0)
    now, before = built["summary"]["now"], built["summary"]["before"]
    assert len(built["nights"]) == now["nights"] == 30
    assert before["nights"] == 30
    assert now["deep_rem_s"] - before["deep_rem_s"] == 20 * 60
    assert now["kwh_total"] == 60.0 and now["cost_p_total"] == 1200
    assert before["kwh_per_night"] == 1.0


def test_tagged_nights_stay_in_and_say_so(db):
    night(db, 0)
    db.save_night_note(NightNote(TODAY.isoformat(), tags=("alcohol", "exercise")), NOON)
    [row] = trends(db, TODAY, 30, tariff_p=None)["nights"]
    assert row["tags"] == ["Alcohol", "Exercise"]
    assert row["left_out"] is True


# --- Over HTTP ----------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime.combine(TODAY, time(9, 0))),
        echo=False,
    )
    night(service.db, 1)
    real_app.state.service = service
    real_app.state.build = "test"
    yield TestClient(real_app)
    service.db.close()


def test_trends_over_http_with_a_tariff(client):
    assert client.get("/api/trends", params={"days": 30}).json()["tariff_p"] is None
    assert client.put("/api/trends/tariff", json={"pence_per_kwh": 24.5}).json() == {"tariff_p": 24.5}
    built = client.get("/api/trends", params={"days": 30}).json()
    assert built["tariff_p"] == 24.5
    assert built["nights"][0]["cost_p"] == round(1.5 * 24.5, 1)


def test_only_the_offered_stretches(client):
    assert client.get("/api/trends", params={"days": 1825}).status_code == 422
    assert client.put("/api/trends/tariff", json={"pence_per_kwh": -3}).status_code == 422


def test_an_older_night_gets_its_kwh_filled_in_from_the_plug(tmp_path):
    """Rows written before kWh was kept are filled in by the hourly pass."""
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime.combine(TODAY, time(9, 0))),
        echo=False,
    )
    morning = TODAY - timedelta(days=1)
    r = run(morning)
    service.db.save_night_run(r, NOON)
    at = r.bedtime_at
    while at < r.wake_at:
        service.db.add_power_sample(at, 170.0)
        at += timedelta(seconds=30)
    service.db.flush_power()

    service.record_missing()
    [filled] = service.db.night_runs(morning.isoformat(), morning.isoformat())
    # Eight hours at 170 W, less the last reading's thirty seconds.
    assert filled.kwh == pytest.approx(1.36, abs=0.01)
    service.db.close()
