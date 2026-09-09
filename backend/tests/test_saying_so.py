"""Saying it, not just colouring it in.

Three gaps, all found by pulling plugs out of walls on the real Pi rather than by
anything in this file, and all the same shape: something the system knew and did
not say where it would be read.

The journal is what you have at 7am over SSH after a bad night. The app is nicer
and it is not there.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.events import EventLog
from hydrosnooze.main import QuietExpectedDisconnects
from hydrosnooze.notify import Notifier
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 9, 3, 0)


@pytest.fixture
def events():
    return EventLog(VirtualClock(NOW))


# --- Everything reaches the journal -------------------------------------------


def test_a_warning_reaches_the_journal(events, caplog):
    with caplog.at_level(logging.WARNING, logger="hydrosnooze.events"):
        events.warning("blaster", "The blaster is not answering")
    assert "The blaster is not answering" in caplog.text


def test_recovery_reaches_the_journal_too(events, caplog):
    """The half that was missing. Breakage was logged by the adapters, mending
    was only ever an event, so the journal told you the bad news and not the
    good and you could not tell a dead device from a mended one."""
    with caplog.at_level(logging.INFO, logger="hydrosnooze.events"):
        events.info("blaster", "The blaster is answering again")
    assert "The blaster is answering again" in caplog.text


def test_the_level_carries_across(events, caplog):
    with caplog.at_level(logging.INFO, logger="hydrosnooze.events"):
        events.info("service", "up")
        events.warning("service", "wobbly")
        events.error("service", "broken")
    assert [r.levelname for r in caplog.records] == ["INFO", "WARNING", "ERROR"]


def test_the_kind_is_kept_so_a_line_says_what_it_is_about(events, caplog):
    with caplog.at_level(logging.WARNING, logger="hydrosnooze.events"):
        events.warning("plug", "gone")
    assert "plug: gone" in caplog.text


def test_events_read_back_from_the_database_are_not_logged_again(events, caplog):
    """Startup seeds a couple of hundred old events. Replaying yesterday into
    tonight's journal would bury tonight."""
    from hydrosnooze.events import Event

    old = [Event(id=1, at=NOW, level="error", kind="stage", message="last week")]
    with caplog.at_level(logging.INFO, logger="hydrosnooze.events"):
        events.seed(old)
    assert caplog.text == ""


# --- A lost plug says so ------------------------------------------------------


def plug_events(service: Service) -> list:
    return [e for e in service.events.recent(20) if e.kind == "plug"]


@pytest.mark.asyncio
async def test_a_plug_that_stops_answering_says_so(tmp_path, monkeypatch):
    """It never did. The blaster always has, which was the wrong way round: the
    plug is the only thing here that measures anything."""
    service = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    await service._sample_power()  # a first, working read

    monkeypatch.setattr(service.power, "read_watts", lambda: _none())
    await service._sample_power()

    said = plug_events(service)
    assert said and said[0].level == "warning"
    assert "not answering" in said[0].message


@pytest.mark.asyncio
async def test_and_it_reaches_the_phone(tmp_path, monkeypatch):
    service = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    await service._sample_power()
    monkeypatch.setattr(service.power, "read_watts", lambda: _none())
    await service._sample_power()

    notifier = Notifier(VirtualClock(NOW), topic="t")
    assert notifier.worth_sending(plug_events(service)[0])


@pytest.mark.asyncio
async def test_a_plug_that_comes_back_says_that_too(tmp_path, monkeypatch):
    service = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    await service._sample_power()
    monkeypatch.setattr(service.power, "read_watts", lambda: _none())
    await service._sample_power()
    monkeypatch.undo()
    await service._sample_power()

    assert "answering again" in plug_events(service)[0].message


@pytest.mark.asyncio
async def test_a_plug_that_stays_gone_says_it_once(tmp_path, monkeypatch):
    """Every thirty seconds, forever, would be the notifier teaching me to
    ignore it by Thursday."""
    service = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    await service._sample_power()
    monkeypatch.setattr(service.power, "read_watts", lambda: _none())
    for _ in range(10):
        await service._sample_power()

    assert len(plug_events(service)) == 1


async def _none():
    return None


# --- One traceback, filtered --------------------------------------------------


def record(message: str) -> logging.LogRecord:
    return logging.LogRecord("aioesphomeapi.connection", logging.ERROR, "", 0, message, (), None)


def test_the_expected_traceback_is_dropped():
    """It is what unplugging the blaster looks like from the inside, and the
    reconnect handles it. A journal full of tracebacks for handled things is one
    where a real one is harder to see."""
    quiet = QuietExpectedDisconnects()
    assert not quiet.filter(record("hydrosnooze-ir @ 192.168.1.178: disconnect request failed"))


def test_everything_else_still_comes_through():
    """Narrow on purpose. Silencing the library wholesale would hide the next
    thing it has to say, which might be the one that matters."""
    quiet = QuietExpectedDisconnects()
    assert quiet.filter(record("192.168.1.178: Connection error occurred: Errno 113"))
    assert quiet.filter(record("Invalid encryption key"))
    assert quiet.filter(record("Unexpected error"))
