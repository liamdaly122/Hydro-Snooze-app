"""Withings and the Health Report, over real HTTP.

The same arrangement as test_tonight_routes: the real app and routes, with a
service this test controls put in place directly, so no lifespan runs, no .env
is read for hardware and no loop starts. Withings is the fake from
withings_fake.py.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from withings_fake import NOW, FakeWithings

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.main import app as real_app
from hydrosnooze.service import Service
from hydrosnooze.withings import sync as sync_module
from hydrosnooze.withings.sync import WithingsSync

HOST = {"host": "hydrosnooze.local:8000"}


@pytest.fixture(autouse=True)
def clock_is_set(monkeypatch):
    monkeypatch.setattr(sync_module.clocksync, "synchronised", lambda: True)


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(), echo=False)
    svc.withings = WithingsSync(
        Settings(withings_client_id="fake-id", withings_client_secret="fake-secret"),
        svc.db,
        svc.events,
        client=FakeWithings().client(),
        wall=lambda: float(NOW),
    )
    svc.started_fetching = []
    monkeypatch.setattr(svc.withings, "sync_soon", lambda: svc.started_fetching.append(True))
    real_app.state.service = svc
    real_app.state.build = "test"
    yield svc
    svc.db.close()


@pytest.fixture
def client(service):
    return TestClient(real_app)


def connect(client) -> httpx.Response:
    sent = client.get("/api/withings/connect", headers=HOST, follow_redirects=False)
    state = dict(httpx.URL(sent.headers["location"]).params)["state"]
    return client.get(
        "/api/withings/callback",
        params={"code": "the-code", "state": state},
        headers=HOST,
        follow_redirects=False,
    )


def test_status_before_anything(client):
    body = client.get("/api/withings").json()
    assert body["configured"] is True and body["connected"] is False
    assert body["latest_night"] is None


def test_not_set_up_is_said_in_words(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(), echo=False)
    real_app.state.service = svc
    got = TestClient(real_app).get("/api/withings/connect", headers=HOST, follow_redirects=False)
    assert got.status_code == 400
    assert "HS_WITHINGS_CLIENT_ID" in got.text
    svc.db.close()


def test_connect_sends_the_browser_to_withings_and_back_to_the_address_it_came_from(client):
    sent = client.get("/api/withings/connect", headers=HOST, follow_redirects=False)
    assert sent.status_code == 302
    location = httpx.URL(sent.headers["location"])
    assert location.host == "account.withings.com"
    assert location.params["redirect_uri"] == "http://hydrosnooze.local:8000/api/withings/callback"


def test_connect_from_an_address_withings_cannot_return_to_says_where_to_go(client):
    got = client.get(
        "/api/withings/connect", headers={"host": "192.168.1.50:8000"}, follow_redirects=False
    )
    assert got.status_code == 400
    assert "http://hydrosnooze.local:8000" in got.text


def test_the_callback_keeps_the_tokens_and_starts_fetching(client, service):
    back = connect(client)
    assert back.status_code == 302 and back.headers["location"] == "/?withings=connected"
    assert service.db.withings_account().access_token == "access-1"
    assert service.started_fetching == [True]
    assert client.get("/api/withings").json()["connected"] is True


def test_saying_no_at_withings_comes_back_as_failed_with_the_reason(client):
    back = client.get(
        "/api/withings/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert back.headers["location"] == "/?withings=failed"
    assert "access_denied" in client.get("/api/withings").json()["last_error"]


def test_a_callback_nobody_started_is_refused(client, service):
    back = client.get(
        "/api/withings/callback",
        params={"code": "c", "state": "made-up"},
        follow_redirects=False,
    )
    assert back.headers["location"] == "/?withings=failed"
    assert service.db.withings_account() is None


def test_fetching_now_then_the_health_report(client):
    assert client.get("/api/health-report").status_code == 404
    connect(client)
    fetched = client.post("/api/withings/sync").json()
    assert fetched["asked"] is True and fetched["latest_night"] == "2026-10-25"
    # Straight away again is too soon, and says so rather than asking.
    assert client.post("/api/withings/sync").json()["asked"] is False

    report = client.get("/api/health-report").json()
    assert report["night"]["wake_on"] == "2026-10-25"
    picked = client.get("/api/health-report", params={"date": "2026-10-23"}).json()
    assert picked["night"]["wake_on"] == "2026-10-23"
    assert client.get("/api/health-report", params={"date": "23/10/2026"}).status_code == 422
    assert client.get("/api/health-report", params={"date": "2026-13-45"}).status_code == 422


def test_sleep_timing_answers_before_there_is_any_sleep(client, service):
    built = client.get("/api/sleep-timing").json()
    assert built["nights"] == 0 and built["profile"] is None
    assert built["lights_out"] == service.schedule.bed_time.strftime("%H:%M")
    assert [p["part"] for p in built["parts"]] == ["drift", "deep", "rem", "wake"]


def test_starting_sleep_timing_again_says_so_in_the_log(client, service):
    built = client.post("/api/sleep-timing/forget").json()
    assert built["since"] == service.clock.now().date().isoformat()
    assert client.get("/api/sleep-timing").json()["since"] == built["since"]
    said = [e.message for e in service.events.recent() if e.kind == "sleep_timing"]
    assert said and "starting again" in said[0]


def test_disconnecting_keeps_the_sleep(client):
    connect(client)
    client.post("/api/withings/sync")
    body = client.delete("/api/withings").json()
    assert body["connected"] is False
    assert client.get("/api/health-report").status_code == 200


def test_connecting_and_fetching_never_press_a_button(client, service):
    connect(client)
    client.post("/api/withings/sync")
    client.get("/api/health-report")
    client.delete("/api/withings")
    assert service.transmitter.sent == []
    assert not service._lock.locked()


def test_the_scoreboard_answers_before_anything_is_recorded(client):
    built = client.get("/api/scoreboard").json()
    assert built["recorded"] == 0 and built["nights"] == 0
    assert [p["verdict"] for p in built["parts"]] == ["empty", "empty", "empty"]


def test_the_suggestion_answers_and_refuses_what_it_cannot_do(client):
    got = client.get("/api/suggestion").json()
    assert got["state"] in ("closed", "no_mat") and got["reach"] == 2
    assert client.post("/api/suggestion/accept").status_code == 409
    assert client.post("/api/suggestion/decline").status_code == 409
    assert client.post("/api/suggestion/reach", json={"reach": 5}).status_code == 422
    widened = client.post("/api/suggestion/reach", json={"reach": 3}).json()
    assert widened["reach"] == 3


def test_the_autopilot_switch_over_http(client):
    assert client.get("/api/autopilot/switch").json() == {"on": True}
    assert client.post("/api/autopilot/switch", json={"on": False}).json() == {"on": False}
    assert client.get("/api/autopilot/switch").json() == {"on": False}
    assert client.get("/api/suggestion").json()["state"] == "off"
    assert "suggested" in client.get("/api/tonight").json()
