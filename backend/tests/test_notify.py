"""Notifications, and the rules that stop them being ignored.

A notifier is only useful if the push means something. The night of 9 September
would have produced roughly fourteen thousand identical messages between 2am and
7am, which is the same as none.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from hydrosnooze import watchdog
from hydrosnooze.clock import VirtualClock
from hydrosnooze.events import Event
from hydrosnooze.notify import REPEAT_AFTER, Notifier

NOW = datetime(2026, 9, 9, 2, 0)


def event(level: str, kind: str, message: str, at: datetime = NOW) -> Event:
    return Event(id=1, at=at, level=level, kind=kind, message=message)


@pytest.fixture
def notifier():
    return Notifier(VirtualClock(NOW), topic="test-topic")


# --- What is worth a push -------------------------------------------------------


def test_errors_are_worth_sending(notifier):
    assert notifier.worth_sending(event("error", "stage", "Missed the Deep stage"))


def test_the_ordinary_running_of_a_night_is_not(notifier):
    """Thirty of these a night. A phone buzzing through every stage boundary gets
    muted, and then the one that mattered is muted too."""
    assert not notifier.worth_sending(event("info", "stage", "Deep: 17C in quiet until 02:30"))


def test_a_warning_that_means_the_night_is_wrong_is(notifier):
    assert notifier.worth_sending(
        event("warning", "stage", "The stage:deep step did not land. Retrying every 30s")
    )
    assert notifier.worth_sending(
        event("warning", "blaster", "The blaster at 192.168.1.178 is not answering.")
    )


def test_a_warning_that_does_not_is_left_alone(notifier):
    assert not notifier.worth_sending(event("warning", "precool", "Nothing to do tonight"))


def test_nothing_is_sent_without_a_topic():
    quiet = Notifier(VirtualClock(NOW), topic="")
    assert not quiet.enabled
    assert not quiet.worth_sending(event("error", "stage", "Missed the Deep stage"))


# --- Not saying the same thing forever -------------------------------------------


def test_the_same_problem_is_only_pushed_once(notifier):
    sent = []
    notifier._post = lambda title, message: sent.append(message)

    for _ in range(20):
        notifier.on_event(event("error", "stage", "Not connected to hydrosnooze-ir"))

    assert len(sent) == 0, "on_event should not post inline"


def test_a_repeat_inside_the_window_is_dropped(notifier):
    """The actual arithmetic of the quiet period, without any network."""
    e = event("error", "stage", "Not connected to hydrosnooze-ir @ 192.168.1.178")
    key = f"{e.kind}:{' '.join(e.message.split()[:6])}"

    notifier._sent[key] = NOW
    notifier.clock.jump_to(NOW + REPEAT_AFTER - timedelta(minutes=1))
    assert notifier._sent[key] == NOW

    notifier.clock.jump_to(NOW + REPEAT_AFTER + timedelta(minutes=1))
    assert notifier.clock.now() - notifier._sent[key] > REPEAT_AFTER


def test_a_number_changing_does_not_make_it_a_new_problem(notifier):
    """"No answer for 40s" then "for 70s" is one problem, not two."""
    first = event("warning", "blaster", "The blaster at 192.168.1.178 is not answering. 40s")
    second = event("warning", "blaster", "The blaster at 192.168.1.178 is not answering. 70s")
    key = lambda e: f"{e.kind}:{' '.join(e.message.split()[:6])}"
    assert key(first) == key(second)


def test_two_different_problems_both_get_through(notifier):
    a = event("error", "stage", "Missed the Deep stage at 22:30")
    b = event("error", "power_off", "Pressed power three times and the plug still reads on")
    key = lambda e: f"{e.kind}:{' '.join(e.message.split()[:6])}"
    assert key(a) != key(b)


# --- Failing to notify must never matter -----------------------------------------


@pytest.mark.asyncio
async def test_a_dead_network_does_not_raise(notifier):
    """Taking the night down because a push failed would be worse than the push
    never existing."""
    notifier.server = "http://192.0.2.1:9"
    notifier.timeout = 0.1
    await notifier._post("t", "m")  # must not raise


# --- The watchdog ------------------------------------------------------------------


def test_off_systemd_everything_is_a_no_op(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert watchdog.notify(READY="1") is False
    assert watchdog.ready() is False
    assert watchdog.alive() is False


def test_no_ping_interval_when_nothing_is_watching(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert watchdog.interval_seconds() is None


def test_it_pings_at_half_the_deadline(monkeypatch):
    """Half, so one late ping is survivable rather than fatal."""
    monkeypatch.setenv("WATCHDOG_USEC", "90000000")
    assert watchdog.interval_seconds() == 45.0


def test_a_nonsense_deadline_is_ignored_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "not-a-number")
    assert watchdog.interval_seconds() is None


def test_it_really_sends_on_a_socket(tmp_path, monkeypatch):
    """The one that proves the protocol rather than the arithmetic."""
    import socket

    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(2)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    try:
        assert watchdog.ready() is True
        assert server.recv(64) == b"READY=1"
        assert watchdog.alive() is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()
        os.unlink(path)
