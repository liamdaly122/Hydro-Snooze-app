"""The loop that fetches sleep: what it asks, what it keeps, and what it never does.

The one rule above the others is that the bed never depends on any of this, so
the last test here holds the command lock and fetches anyway.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from withings_fake import NOW, FakeWithings

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.events import EventLog
from hydrosnooze.notify import Notifier
from hydrosnooze.service import Service
from hydrosnooze.withings import sync as sync_module
from hydrosnooze.withings.sync import BACKFILL_S, ConnectProblem, WithingsSync

CONFIGURED = Settings(withings_client_id="fake-id", withings_client_secret="fake-secret")
HOST = "hydrosnooze.local:8000"


class Wall:
    """Real time, held still, and moved by hand."""

    def __init__(self, at: int = NOW) -> None:
        self.at = at

    def __call__(self) -> float:
        return float(self.at)


@pytest.fixture(autouse=True)
def clock_is_set(monkeypatch):
    monkeypatch.setattr(sync_module.clocksync, "synchronised", lambda: True)


@pytest.fixture
def rig(tmp_path):
    fake = FakeWithings()
    db = Database(tmp_path / "s.db")
    events = EventLog(VirtualClock(datetime(2026, 10, 27, 12, 0)))
    wall = Wall()
    sync = WithingsSync(CONFIGURED, db, events, client=fake.client(), wall=wall)

    class Rig:
        pass

    r = Rig()
    r.fake, r.db, r.events, r.wall, r.sync = fake, db, events, wall, sync
    yield r
    db.close()


async def connect(r) -> None:
    url = r.sync.begin_connect(HOST)
    state = dict(httpx.URL(url).params)["state"]
    await r.sync.finish_connect("the-code", state)


# --- Connecting --------------------------------------------------------------------


async def test_connecting_trades_the_code_in_at_once_and_keeps_the_tokens(rig):
    url = rig.sync.begin_connect(HOST)
    params = dict(httpx.URL(url).params)
    assert url.startswith("https://account.withings.com/oauth2_user/authorize2?")
    assert params["redirect_uri"] == "http://hydrosnooze.local:8000/api/withings/callback"
    assert params["scope"] == "user.activity"

    await rig.sync.finish_connect("the-code", params["state"])
    exchange = rig.fake.calls_to("requesttoken")[0]["fields"]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "the-code"
    assert exchange["redirect_uri"] == params["redirect_uri"]

    account = rig.db.withings_account()
    assert account.access_token == "access-1"
    assert account.expires_at == NOW + 10800
    assert account.last_update == NOW - BACKFILL_S
    assert "connected" in rig.events.recent(1)[0].message


async def test_a_sign_in_not_started_here_is_refused_without_asking_withings(rig):
    with pytest.raises(ConnectProblem):
        await rig.sync.finish_connect("the-code", "somebody-elses-state")
    assert rig.fake.calls == []


async def test_a_sign_in_can_only_be_finished_once(rig):
    url = rig.sync.begin_connect(HOST)
    state = dict(httpx.URL(url).params)["state"]
    await rig.sync.finish_connect("the-code", state)
    with pytest.raises(ConnectProblem):
        await rig.sync.finish_connect("the-code", state)


async def test_a_sign_in_left_ten_minutes_has_gone(rig):
    url = rig.sync.begin_connect(HOST)
    rig.wall.at += 11 * 60
    with pytest.raises(ConnectProblem):
        await rig.sync.finish_connect("the-code", dict(httpx.URL(url).params)["state"])


def test_an_address_withings_cannot_send_you_back_to_is_refused_in_words(rig):
    with pytest.raises(ConnectProblem, match="hydrosnooze.local:8000"):
        rig.sync.begin_connect("192.168.1.50:8000")


def test_not_set_up_says_which_two_lines_are_missing(tmp_path):
    db = Database(tmp_path / "s.db")
    sync = WithingsSync(Settings(), db, EventLog(VirtualClock()))
    with pytest.raises(ConnectProblem, match="HS_WITHINGS_CLIENT_ID"):
        sync.begin_connect(HOST)
    assert sync.status()["configured"] is False
    db.close()


# --- Fetching ----------------------------------------------------------------------


async def test_the_first_pass_fetches_the_last_month_and_keeps_every_night(rig):
    await connect(rig)
    assert await rig.sync.sync()
    held = rig.db.sleep_nights()
    assert [n.wake_on for n in held] == [n["date"] for n in rig.fake.nights]
    assert rig.db.withings_account().last_update == max(n["modified"] for n in rig.fake.nights)
    assert rig.sync.last_error is None
    assert rig.sync.status()["latest_night"] == "2026-10-25"
    assert "Sleep arrived" in rig.events.recent(1)[0].message


async def test_a_second_pass_asks_only_about_what_changed(rig):
    await connect(rig)
    await rig.sync.sync()
    before = len(rig.fake.calls_to("get"))
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    # The newest night comes back, because lastupdate is not strictly after,
    # but it has not changed, so its detail is not asked for again.
    assert len(rig.fake.calls_to("getsummary")) == 2
    assert len(rig.fake.calls_to("get")) == before


async def test_a_night_that_changes_is_fetched_again_and_replaced(rig):
    await connect(rig)
    await rig.sync.sync()
    rig.fake.nights[-1]["modified"] += 3600
    rig.fake.nights[-1]["data"]["sleep_score"] = 42
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert len(rig.db.sleep_nights()) == 4
    assert rig.db.sleep_night_on("2026-10-25").data["sleep_score"] == 42


async def test_paging_follows_more_and_takes_the_servers_offset(rig):
    await connect(rig)
    rig.fake.page = 3
    rig.fake.dropped = {1}  # cut from page one after it was counted
    await rig.sync.sync()
    offsets = [c["fields"].get("offset") for c in rig.fake.calls_to("getsummary")]
    assert offsets == [None, "3"]
    held = [n.wake_on for n in rig.db.sleep_nights()]
    assert held == ["2026-10-22", "2026-10-24", "2026-10-25"]


async def test_a_token_near_its_end_is_refreshed_and_kept_before_it_is_used(rig):
    await connect(rig)
    rig.wall.at = rig.db.withings_account().expires_at - 5 * 60
    kept_first: list[bool] = []
    rig.fake.on_sleep_call = lambda token: kept_first.append(
        rig.db.withings_account().access_token == token
    )
    await rig.sync.sync()
    refresh = rig.fake.calls_to("requesttoken")[1]["fields"]
    assert refresh["grant_type"] == "refresh_token"
    assert refresh["refresh_token"] == "refresh-1"
    assert rig.db.withings_account().refresh_token == "refresh-2"
    assert kept_first and all(kept_first)


async def test_a_refused_token_is_refreshed_once_and_tried_again(rig):
    await connect(rig)
    rig.fake.refuse["getsummary"] = [401]
    await rig.sync.sync()
    assert len(rig.fake.calls_to("requesttoken")) == 2
    assert len(rig.db.sleep_nights()) == 4


async def test_status_100_twice_is_no_sleep_rather_than_a_broken_connection(rig):
    await connect(rig)
    rig.fake.refuse["getsummary"] = [100, 100]
    await rig.sync.sync()
    assert rig.db.sleep_nights() == []
    assert rig.sync.last_error is None
    assert not rig.db.withings_account().needs_reconnect


async def test_a_refused_refresh_says_reconnect_once_keeps_trying_and_wakes_nobody(rig):
    await connect(rig)
    notifier = Notifier(rig.events.clock, topic="a-topic")
    rig.wall.at = rig.db.withings_account().expires_at
    rig.fake.refuse["requesttoken"] = [503, 503]

    await rig.sync.sync()
    assert rig.db.withings_account().needs_reconnect
    warning = rig.events.recent(1)[0]
    assert warning.level == "warning" and "reconnected" in warning.message
    assert not notifier.worth_sending(warning)

    rig.wall.at += 30 * 60
    await rig.sync.sync()
    warnings = [e for e in rig.events.recent(50) if e.level == "warning"]
    assert len(warnings) == 1

    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert not rig.db.withings_account().needs_reconnect
    assert "again" in rig.events.recent(2)[1].message
    assert len(rig.db.sleep_nights()) == 4


async def test_withings_out_of_reach_is_a_missing_chart_and_nothing_louder(rig):
    await connect(rig)
    rig.fake.unreachable = httpx.ConnectError("no route to host")
    before = len(rig.events.recent(50))
    assert await rig.sync.sync()
    assert rig.sync.last_error.startswith("Could not reach Withings")
    assert len(rig.events.recent(50)) == before


async def test_too_many_requests_means_an_hour_off(rig):
    await connect(rig)
    rig.fake.refuse["getsummary"] = [601]
    await rig.sync.sync()
    asked = len(rig.fake.calls)
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert len(rig.fake.calls) == asked
    rig.wall.at += 31 * 60
    await rig.sync.sync()
    assert len(rig.db.sleep_nights()) == 4


async def test_nothing_is_asked_until_the_clock_has_been_set(rig, monkeypatch):
    await connect(rig)
    monkeypatch.setattr(sync_module.clocksync, "synchronised", lambda: False)
    asked = len(rig.fake.calls)
    assert not await rig.sync.sync()
    assert len(rig.fake.calls) == asked
    assert rig.sync.status()["waiting_for_clock"] is True


async def test_asking_again_inside_ten_minutes_does_nothing(rig):
    await connect(rig)
    await rig.sync.sync()
    asked = len(rig.fake.calls)
    rig.wall.at += 5 * 60
    assert not await rig.sync.sync(manual=True)
    assert len(rig.fake.calls) == asked
    rig.wall.at += 6 * 60
    assert await rig.sync.sync(manual=True)


async def test_disconnecting_keeps_the_nights_and_stops_the_asking(rig):
    await connect(rig)
    await rig.sync.sync()
    rig.sync.disconnect()
    asked = len(rig.fake.calls)
    rig.wall.at += 60 * 60
    assert not await rig.sync.sync()
    assert len(rig.fake.calls) == asked
    assert len(rig.db.sleep_nights()) == 4
    assert rig.sync.status()["connected"] is False


# --- The bed never waits -----------------------------------------------------------


async def test_fetching_sleep_never_waits_for_the_command_lock(tmp_path):
    service = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(), echo=False)
    fake = FakeWithings()
    service.withings = WithingsSync(
        CONFIGURED, service.db, service.events, client=fake.client(), wall=Wall()
    )
    url = service.withings.begin_connect(HOST)
    await service.withings.finish_connect("code", dict(httpx.URL(url).params)["state"])

    async with service._lock:
        # A press sequence is in flight and holds the lock for as long as it
        # takes. Sleep has to arrive regardless.
        assert await asyncio.wait_for(service.withings.sync(), timeout=5)
    assert len(service.db.sleep_nights()) == 4
    service.db.close()


def test_a_service_without_withings_has_no_withings_loop(tmp_path):
    service = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(), echo=False)
    assert service.withings.configured is False
    service.db.close()


# --- What an unfinished night looks like ------------------------------------------


def observed(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Withings observed" in r.getMessage()]


async def test_an_unfinished_night_is_written_down_once_and_again_when_it_finishes(rig, caplog):
    await connect(rig)
    caplog.set_level("INFO", logger="hydrosnooze.withings.sync")
    rig.fake.nights[-1]["completed"] = False
    await rig.sync.sync()
    first = observed(caplog)
    assert len(first) == 1 and "not completed yet" in first[0]

    rig.fake.nights[-1]["modified"] += 600
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert len(observed(caplog)) == 1, "still unfinished, and said once already"

    rig.fake.nights[-1]["completed"] = True
    rig.fake.nights[-1]["modified"] += 600
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert "completed now" in observed(caplog)[-1]


async def test_a_night_that_grows_after_completed_is_written_down(rig, caplog):
    await connect(rig)
    caplog.set_level("INFO", logger="hydrosnooze.withings.sync")
    await rig.sync.sync()
    rig.fake.nights[-1]["enddate"] += 1200
    rig.fake.nights[-1]["modified"] += 600
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert any("grew after it was marked completed" in m for m in observed(caplog))


async def test_a_new_id_for_the_same_night_is_written_down_and_kept_once(rig, caplog):
    await connect(rig)
    caplog.set_level("INFO", logger="hydrosnooze.withings.sync")
    await rig.sync.sync()
    arrivals = [e for e in rig.events.recent(50) if "Sleep arrived" in e.message]

    rig.fake.nights[-1]["id"] += 1
    rig.fake.nights[-1]["modified"] += 600
    rig.wall.at += 30 * 60
    await rig.sync.sync()
    assert any("came back under a new id" in m for m in observed(caplog))
    assert len(rig.db.sleep_nights()) == 4
    assert [e for e in rig.events.recent(50) if "Sleep arrived" in e.message] == arrivals
