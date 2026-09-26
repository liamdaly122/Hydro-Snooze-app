"""The live socket, which is how the app knows anything is happening at all.

Two findings from the review on 22 September.

**A phone that fell behind was dropped and never told.** When a subscriber's
queue filled, the service stopped writing to it, but the socket handler kept
waiting on that queue and sending a ping every twenty five seconds. So the
connection stayed open, the app kept showing Connected, and nothing on it ever
changed again. The app already knows what to do when a socket closes: reconnect
and backfill. It was just never given a close.

**The first message showed the assumptions.** It sent the bare schedule, with a
head start worked out from an assumed 20C bed, straight after the HTTP endpoint
had sent the measured one. So every reconnect put the wrong pre-heat time back
on the screen. `schedule_as_shown` was written to fix exactly that and this
path had never been moved onto it. /api/tonight had the same problem.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.adapters.probes import RETURN, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.main import app as real_app
from hydrosnooze.models import Schedule, SleepStage, Stage
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 12, 20, 0)


@pytest.fixture
def service(tmp_path):
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=VirtualClock(NOW),
        echo=False,
    )
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[SleepStage(Stage.DEEP, 240, 15), SleepStage(Stage.REM, 210, 22)],
    )
    # A bed already well on its way, which is what makes the measured head start
    # differ from the assumed one.
    svc.probes.readings[RETURN] = Reading(17.0, NOW)
    yield svc
    svc.db.close()


@pytest.fixture
def client(service):
    real_app.state.service = service
    real_app.state.build = "test"
    return TestClient(real_app)


def test_the_first_message_on_the_socket_is_the_schedule_as_measured(client, service):
    assert service.schedule_as_shown() != _bare(service), "the case this is about"
    with client.websocket_connect("/api/live") as ws:
        first = ws.receive_json()
    assert first["schedule"] == service.schedule_as_shown()


def test_tonight_is_drawn_from_the_measured_schedule_too(client, service):
    running = client.get("/api/tonight").json()["running"]
    assert running == _shown_for_tonight(service)
    assert running["preconditioning"]["lead_minutes"] == 18, "measured, not assumed"


def _bare(service):
    from hydrosnooze.api.schemas import schedule_json

    return schedule_json(service.schedule)


def _shown_for_tonight(service):
    from hydrosnooze.api.schemas import schedule_json

    return schedule_json(
        service.tonight_now(), bed_c=service.probes.bed_c, learned=service._learned_lead
    )


@pytest.mark.asyncio
async def test_a_subscriber_that_falls_behind_is_told_rather_than_forgotten(service):
    """The service side of it: once dropped, the handler can find out."""
    queue = service.subscribe()
    for n in range(80):
        service._broadcast({"n": n})
    assert not service.still_subscribed(queue)


@pytest.mark.asyncio
async def test_the_socket_closes_when_its_subscriber_has_been_dropped(service):
    """So the app sees a close, reconnects, and backfills, rather than showing
    Connected over a frozen screen."""
    from hydrosnooze.main import live

    sent: list[dict] = []
    closed = asyncio.Event()

    class FakeSocket:
        # What every real socket carries and the sign-in check reads. No
        # address, no origin and no cookie is the home network with no
        # password, which is what this service has.
        client = None
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}

        def __init__(self):
            self.app = type("A", (), {"state": type("S", (), {"service": service})()})()

        async def accept(self):
            pass

        async def send_json(self, payload):
            sent.append(payload)
            if len(sent) == 1:
                # The phone goes to sleep: the service keeps talking and the
                # queue fills far past its limit before anything is read.
                for n in range(80):
                    service._broadcast({"n": n})

        async def close(self, code: int = 1000):
            closed.set()

    await asyncio.wait_for(live(FakeSocket()), timeout=2)
    assert closed.is_set()
    # It does not bother delivering a backlog it has already given up on.
    assert len(sent) < 10, len(sent)
