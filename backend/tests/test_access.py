"""Signing in, and what arrives through Tailscale. See access.py.

Two halves. The rules themselves, against an Access with a clock this test
moves, and then the same rules over real HTTP, because the check lives in
main.py's middleware and a rule nobody routes through is not a rule.

The addresses matter here in a way they do nowhere else in the suite. The test
client normally calls itself "testclient", which is not an address and so counts
as the home network. The Tailscale tests give it a tailnet address, or the
Pi's own address with the tunnel switched on, which is what `tailscale serve`
looks like from inside.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hydrosnooze.access import (
    COOKIE,
    NO_PASSWORD,
    SESSION_LASTS,
    TRIES,
    Access,
    SignInRefused,
    device_label,
    hash_password,
    password_matches,
)
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.events import EventLog
from hydrosnooze.main import app as real_app
from hydrosnooze.models import Power
from hydrosnooze.notify import Notifier
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 25, 21, 0)
PASSWORD = "correct horse battery"
KEY = "the-scripts-key-0123456789"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)

TAILNET_PHONE = ("100.101.102.103", 50000)
THE_PI = ("127.0.0.1", 50000)
ON_THE_WIFI = ("192.168.1.40", 50000)


class Pushed(Notifier):
    """A notifier that writes down what it would have sent."""

    def __init__(self) -> None:
        super().__init__(VirtualClock(NOW), topic="t")
        self.sent: list[tuple[str, str, str]] = []

    def push(self, title: str, message: str, *, tag: str = "warning") -> None:
        self.sent.append((title, message, tag))


class Wall:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def make(tmp_path, **settings) -> tuple[Access, Wall, Pushed, Database]:
    db = Database(tmp_path / "a.db")
    wall = Wall()
    pushed = Pushed()
    access = Access(
        Settings(db_path=str(tmp_path / "a.db"), **settings),
        db,
        EventLog(VirtualClock(NOW)),
        pushed,
        wall=wall,
    )
    return access, wall, pushed, db


HASH = hash_password(PASSWORD)


# --- The password ------------------------------------------------------------------


def test_a_password_is_kept_as_a_hash_that_only_it_matches():
    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert password_matches(PASSWORD, stored)
    assert not password_matches("correct horse battery ", stored)
    assert not password_matches(PASSWORD, "not a hash at all")
    # Two hashes of the same password differ, so the file says nothing about
    # whether two machines share one.
    assert hash_password(PASSWORD) != stored


def test_the_script_that_sets_the_password_writes_what_the_service_checks():
    """scripts/password.py runs without the service's packages, so it has its
    own copy of the hash. This is what stops the two drifting apart."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "password.py"
    spec = importlib.util.spec_from_file_location("password_script", path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    written = script.hash_password(PASSWORD)
    assert password_matches(PASSWORD, written)
    assert not password_matches("something else entirely", written)
    # Nothing a shell or systemd would read as a variable or a comment.
    assert "$" not in written and "#" not in written


def test_a_device_is_named_the_way_a_person_would():
    assert device_label(IPHONE) == "iPhone, Safari"
    assert device_label("") == "an unknown browser"


# --- Which way it came in ---------------------------------------------------------------


def test_the_home_network_is_home_and_the_tailnet_is_not(tmp_path):
    access, *_ = make(tmp_path)
    assert access.via("192.168.1.40") == "home"
    assert access.via("100.101.102.103") == "tailscale"
    assert access.via("::ffff:100.101.102.103") == "tailscale"
    assert access.via("fd7a:115c:a1e0::1234") == "tailscale"
    # Not an address at all: the test client, a unix socket.
    assert access.via("testclient") == "home"
    assert access.via(None) == "home"


def test_the_pis_own_address_is_tailscale_once_the_tunnel_is_on(tmp_path):
    """tailscale serve hands everything over from 127.0.0.1. Trusting that
    address would be trusting the whole tailnet."""
    access, *_ = make(tmp_path)
    assert access.via("127.0.0.1") == "home"

    access, *_ = make(tmp_path, tunnel="tailscale")
    assert access.via("127.0.0.1") == "tailscale"
    assert access.via("::1") == "tailscale"


def test_the_scripts_key_is_the_one_way_to_be_the_pi(tmp_path):
    access, *_ = make(tmp_path, tunnel="tailscale", api_key=KEY, password_hash=HASH)
    assert access.via("127.0.0.1", f"Bearer {KEY}") == "home"
    assert access.admit(client_host="127.0.0.1", authorization=f"Bearer {KEY}").allowed
    assert not access.admit(client_host="127.0.0.1", authorization="Bearer nope").allowed


# --- No password -------------------------------------------------------------------------


def test_with_no_password_home_works_as_it_always_has(tmp_path):
    access, *_ = make(tmp_path, tunnel="tailscale")
    assert access.admit(client_host="192.168.1.40").allowed


def test_with_no_password_nothing_gets_in_through_tailscale(tmp_path):
    """Switching Tailscale on before setting a password fails shut."""
    access, *_ = make(tmp_path, tunnel="tailscale")
    for host in ("100.101.102.103", "127.0.0.1"):
        verdict = access.admit(client_host=host)
        assert not verdict.allowed
        assert verdict.status == 403
        assert verdict.reason == NO_PASSWORD
    with pytest.raises(SignInRefused) as refused:
        access.sign_in(PASSWORD, client_host="100.101.102.103")
    assert refused.value.status == 403


# --- Signing in ---------------------------------------------------------------------------


def test_a_password_is_needed_everywhere_once_there_is_one(tmp_path):
    access, *_ = make(tmp_path, password_hash=HASH)
    for host in ("192.168.1.40", "100.101.102.103"):
        verdict = access.admit(client_host=host)
        assert not verdict.allowed and verdict.status == 401


def test_signing_in_hands_back_a_token_that_lets_the_device_in(tmp_path):
    access, _, pushed, db = make(tmp_path, password_hash=HASH)
    token = access.sign_in(PASSWORD, client_host="100.101.102.103", user_agent=IPHONE)

    assert access.admit(client_host="100.101.102.103", cookie=token).allowed
    assert not access.admit(client_host="100.101.102.103", cookie=token + "x").allowed
    # Only a hash of the token is kept.
    assert db.session(token) is None
    # And a sign-in is said out loud, where it came from and what it was.
    title, message, _ = pushed.sent[-1]
    assert title == "HydroSnooze sign-in"
    assert "iPhone, Safari signed in through Tailscale" in message


def test_the_wrong_password_signs_nobody_in(tmp_path):
    access, *_ = make(tmp_path, password_hash=HASH)
    with pytest.raises(SignInRefused) as refused:
        access.sign_in("guess", client_host="192.168.1.40")
    assert refused.value.status == 401


def test_too_many_wrong_passwords_pause_signing_in_even_for_the_right_one(tmp_path):
    access, wall, pushed, _ = make(tmp_path, password_hash=HASH)
    for _ in range(TRIES):
        with pytest.raises(SignInRefused):
            access.sign_in("guess", client_host="100.101.102.103")

    with pytest.raises(SignInRefused) as refused:
        access.sign_in(PASSWORD, client_host="100.101.102.103")
    assert refused.value.status == 429
    assert "paused" in pushed.sent[-1][1]

    wall.now += timedelta(minutes=16)
    assert access.sign_in(PASSWORD, client_host="100.101.102.103")


def test_wrong_passwords_spread_out_never_add_up_to_a_pause(tmp_path):
    access, wall, *_ = make(tmp_path, password_hash=HASH)
    for _ in range(TRIES * 2):
        with pytest.raises(SignInRefused) as refused:
            access.sign_in("guess", client_host="192.168.1.40")
        assert refused.value.status == 401
        wall.now += timedelta(minutes=5)


def test_a_session_lasts_half_a_year_from_when_it_was_last_used(tmp_path):
    access, wall, _, db = make(tmp_path, password_hash=HASH)
    token = access.sign_in(PASSWORD, client_host="192.168.1.40")

    # Used every few months, it never runs out.
    for _ in range(4):
        wall.now += SESSION_LASTS - timedelta(days=1)
        assert access.admit(client_host="192.168.1.40", cookie=token).allowed

    wall.now += SESSION_LASTS + timedelta(days=1)
    assert not access.admit(client_host="192.168.1.40", cookie=token).allowed
    assert db.session_count() == 0


def test_last_use_is_written_down_twice_a_day_at_most(tmp_path):
    """Every request writing to the SD card for a date nobody reads to the
    minute would be the wrong trade."""
    access, wall, _, db = make(tmp_path, password_hash=HASH)
    token = access.sign_in(PASSWORD, client_host="192.168.1.40")
    writes: list[datetime] = []
    real = db.session_seen
    db.session_seen = lambda h, at: (writes.append(at), real(h, at))[1]

    # A day and a bit of a phone asking every five minutes.
    for _ in range(300):
        wall.now += timedelta(minutes=5)
        access.admit(client_host="192.168.1.40", cookie=token)
    assert 0 < len(writes) <= 1 + (300 * 5) // (12 * 60)


def test_changing_the_password_signs_every_device_out(tmp_path):
    access, *_ = make(tmp_path, password_hash=HASH)
    token = access.sign_in(PASSWORD, client_host="192.168.1.40")

    access.settings = Settings(password_hash=hash_password("a different password"))
    assert not access.admit(client_host="192.168.1.40", cookie=token).allowed


def test_signing_out_everywhere_means_everywhere(tmp_path):
    access, *_ = make(tmp_path, password_hash=HASH)
    phone = access.sign_in(PASSWORD, client_host="100.101.102.103")
    laptop = access.sign_in(PASSWORD, client_host="192.168.1.40")

    access.sign_out(laptop)
    assert not access.admit(client_host="192.168.1.40", cookie=laptop).allowed
    assert access.admit(client_host="100.101.102.103", cookie=phone).allowed

    access.sign_out_everywhere()
    assert not access.admit(client_host="100.101.102.103", cookie=phone).allowed


def test_the_live_socket_only_opens_for_the_apps_own_page(tmp_path):
    access, *_ = make(tmp_path, public_url="https://hydrosnooze.tail1234.ts.net")
    assert access.same_origin(None, "hydrosnooze.local:8000", None)
    assert access.same_origin("http://hydrosnooze.local:8000", "hydrosnooze.local:8000", None)
    assert access.same_origin(
        "https://hydrosnooze.tail1234.ts.net", "127.0.0.1:8000", None
    )
    assert not access.same_origin("https://evil.example", "hydrosnooze.local:8000", None)


# --- Over HTTP -----------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path):
    svc = Service(
        Settings(
            db_path=str(tmp_path / "s.db"),
            password_hash=HASH,
            api_key=KEY,
            tunnel="tailscale",
            public_url="https://hydrosnooze.tail1234.ts.net",
        ),
        clock=VirtualClock(NOW),
        echo=False,
    )
    real_app.state.service = svc
    real_app.state.build = "test"
    yield svc
    svc.db.close()


def outside() -> TestClient:
    """A phone on the tailnet. Always https, because `tailscale serve` is."""
    return TestClient(real_app, base_url="https://testserver", client=TAILNET_PHONE)


def signed_in(client: TestClient) -> TestClient:
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"user-agent": IPHONE})
    assert r.status_code == 200, r.text
    return client


def test_every_route_but_signing_in_needs_a_session(service):
    client = TestClient(real_app, client=ON_THE_WIFI)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/schedule").status_code == 401
    assert client.post("/api/power/press").status_code == 401
    assert client.get("/api/auth").json() == {
        "required": True,
        "signed_in": False,
        "via": "home",
        "refused": None,
    }

    signed_in(client)
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/auth").json()["signed_in"] is True


def test_the_cookie_is_out_of_reach_of_scripts_and_other_sites(service):
    r = outside().post("/api/auth/login", json={"password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


def test_on_the_home_network_the_cookie_is_not_marked_secure(service):
    """Plain http at home. A Secure cookie there is never sent back, and the
    phone would be asked for the password on every page."""
    client = TestClient(real_app, client=ON_THE_WIFI)
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_the_scripts_on_the_pi_get_in_with_the_key_and_nothing_else(service):
    client = TestClient(real_app, client=THE_PI)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"authorization": f"Bearer {KEY}"}).status_code == 200


def test_signing_out_everywhere_over_http(service):
    client = signed_in(outside())
    other = signed_in(TestClient(real_app, client=ON_THE_WIFI))
    assert client.post("/api/auth/logout-everywhere").status_code == 200
    assert other.get("/api/state").status_code == 401


def test_the_power_button_from_outside_the_house_asks_the_plug_first(service, monkeypatch):
    """One blind press on a unit that is already off switches it on with nobody
    there. From outside, the tap sends the checked command instead."""
    client = signed_in(outside())
    calls: list[str] = []

    async def on() -> None:
        calls.append("on")

    async def off() -> None:
        calls.append("off")

    async def press() -> None:
        calls.append("press")

    monkeypatch.setattr(service, "power_on", on)
    monkeypatch.setattr(service, "power_off", off)
    monkeypatch.setattr(service, "press_power", press)

    service.state.power = Power.ON
    assert client.post("/api/power/press").status_code == 200
    service.state.power = Power.OFF
    assert client.post("/api/power/press").status_code == 200
    assert calls == ["off", "on"]

    service.state.power = Power.UNKNOWN
    r = client.post("/api/power/press")
    assert r.status_code == 409
    assert "press power blind" in r.json()["detail"]
    assert calls == ["off", "on"]


def test_at_home_the_power_button_is_still_one_press(service, monkeypatch):
    client = signed_in(TestClient(real_app, client=ON_THE_WIFI))
    calls: list[str] = []

    async def press() -> None:
        calls.append("press")

    monkeypatch.setattr(service, "press_power", press)
    service.state.power = Power.ON
    assert client.post("/api/power/press").status_code == 200
    assert calls == ["press"]


def test_info_says_how_far_the_bed_is_from_utc(service):
    client = signed_in(outside())
    info = client.get("/api/info").json()
    assert isinstance(info["utc_offset_minutes"], int)
    assert info["via"] == "tailscale"


def test_the_live_feed_needs_signing_in_too(service):
    client = outside()
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/live") as socket:
        socket.receive_json()

    # Passed by hand: the test client does not carry its cookies onto a
    # socket the way a browser does.
    signed_in(client)
    cookie = {"cookie": f"{COOKIE}={client.cookies[COOKIE]}"}
    with client.websocket_connect("/api/live", headers=cookie) as socket:
        assert "state" in socket.receive_json()


def test_the_live_feed_refuses_another_sites_page(service):
    client = signed_in(outside())
    cookie = f"{COOKIE}={client.cookies[COOKIE]}"
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/api/live", headers={"cookie": cookie, "origin": "https://evil.example"}
    ) as s:
        s.receive_json()
    # The same socket from the app's own page opens.
    with client.websocket_connect(
        "/api/live", headers={"cookie": cookie, "origin": "https://hydrosnooze.tail1234.ts.net"}
    ) as s:
        assert "state" in s.receive_json()


def test_the_withings_sign_in_comes_back_to_the_tailscale_address(service):
    from withings_fake import FakeWithings

    from hydrosnooze.withings.sync import WithingsSync

    service.withings = WithingsSync(
        Settings(withings_client_id="fake-id", withings_client_secret="fake-secret"),
        service.db,
        service.events,
        client=FakeWithings().client(),
    )
    client = signed_in(outside())
    r = client.get("/api/withings/connect", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert (
        "redirect_uri=https%3A%2F%2Fhydrosnooze.tail1234.ts.net%2Fapi%2Fwithings%2Fcallback"
        in r.headers["location"]
    )


def test_withings_through_tailscale_says_what_is_missing_without_the_address():
    """No HS_PUBLIC_URL, so no way back from Withings that the phone can reach.
    Said in words here, rather than by Withings with a page that says nothing."""
    from withings_fake import FakeWithings

    from hydrosnooze.withings.sync import ConnectProblem, WithingsSync

    sync = WithingsSync(
        Settings(withings_client_id="fake-id", withings_client_secret="fake-secret"),
        Database(":memory:"),
        EventLog(VirtualClock(NOW)),
        client=FakeWithings().client(),
    )
    with pytest.raises(ConnectProblem) as problem:
        sync.begin_connect("hydrosnooze.tail1234.ts.net", public_url=None, outside=True)
    assert "HS_PUBLIC_URL" in str(problem.value)
