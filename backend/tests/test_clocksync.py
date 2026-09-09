"""Acting on a clock nothing has confirmed.

A Raspberry Pi has no battery-backed clock. At boot it believes it is roughly
whenever it last shut down, which is close enough to look right. A Pi unplugged
since Friday and powered up on Monday evening starts up believing it is Friday
afternoon, schedules a night that ended three days ago, and then jumps three days
forward the moment NTP answers.

The scheduler works in plain local time and cannot tell. So it waits. The part
that needs the most care is that it must not wait forever: a bed that never runs
is a worse outcome than a bed that ran on an unconfirmed clock.
"""

from __future__ import annotations

import pytest

from hydrosnooze import clocksync
from hydrosnooze.config import Settings
from hydrosnooze.service import Service


@pytest.fixture(autouse=True)
def forget():
    clocksync.reset()
    yield
    clocksync.reset()


def service(tmp_path) -> Service:
    return Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)


def answer(monkeypatch, value):
    monkeypatch.setattr(clocksync, "synchronised", lambda: value)


# --- Asking the question ------------------------------------------------------


def test_a_machine_with_its_own_clock_is_not_asked_to_wait(monkeypatch, tmp_path):
    """A Mac has a clock and a battery. There is nothing to wait for, and a guard
    that stopped it working would be a bug rather than a safeguard."""
    answer(monkeypatch, None)
    assert service(tmp_path)._clock_trusted()


def test_a_confirmed_clock_is_trusted(monkeypatch, tmp_path):
    answer(monkeypatch, True)
    assert service(tmp_path)._clock_trusted()


def test_an_unset_clock_holds_everything_up(monkeypatch, tmp_path):
    answer(monkeypatch, False)
    assert not service(tmp_path)._clock_trusted()


def test_the_flag_file_is_enough_on_its_own(monkeypatch, tmp_path):
    """Checking a path costs nothing, so it is tried before any subprocess."""
    flag = tmp_path / "synchronized"
    flag.write_text("")
    monkeypatch.setattr(clocksync, "SYNCED_FLAG", str(flag))
    assert clocksync.synchronised() is True


def test_systemd_present_but_not_running_is_not_an_answer(monkeypatch, tmp_path):
    """Inside a container timedatectl exits non-zero. That is no opinion, which
    is not the same as a no, and must not hold the night up."""
    monkeypatch.setattr(clocksync, "SYNCED_FLAG", str(tmp_path / "nope"))
    monkeypatch.setattr(clocksync.shutil, "which", lambda _: "/usr/bin/timedatectl")

    class Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(clocksync.subprocess, "run", lambda *a, **k: Failed())
    assert clocksync.synchronised() is None


def test_the_answer_sticks_once_it_is_yes(monkeypatch, tmp_path):
    """Losing NTP later does not make the clock wrong, it makes it drift slowly.
    The risk is entirely at boot, so asking again forever would be noise."""
    monkeypatch.setattr(clocksync, "SYNCED_FLAG", str(tmp_path / "flag"))
    calls = []

    def counted():
        calls.append(1)
        return True

    monkeypatch.setattr(clocksync, "_ask", counted)
    assert clocksync.synchronised() is True
    assert clocksync.synchronised() is True
    assert len(calls) == 1


def test_asking_again_is_throttled(monkeypatch, tmp_path):
    """The tick loop runs at 1 Hz. A subprocess a second, to ask a question whose
    answer changes once, would be a poor way to spend a Pi."""
    calls = []
    monkeypatch.setattr(clocksync, "_ask", lambda: calls.append(1) or False)
    for _ in range(20):
        clocksync.synchronised()
    assert len(calls) == 1


# --- Never wedging the bed ----------------------------------------------------


def test_it_gives_up_waiting_rather_than_never_running(monkeypatch, tmp_path):
    """The one that matters. If the clock is never confirmed, the choice is
    between a night at possibly wrong times and no night at all, and the second
    is worse. It runs, and it says so at error level so it reaches the phone."""
    answer(monkeypatch, False)
    s = service(tmp_path)
    assert not s._clock_trusted()

    monkeypatch.setattr(clocksync, "GIVE_UP_AFTER_S", 0)
    assert s._clock_trusted()

    said = [e for e in s.events.recent(10) if e.level == "error"]
    assert said and "may be wrong" in said[0].message


def test_it_says_when_it_starts_waiting(monkeypatch, tmp_path):
    answer(monkeypatch, False)
    s = service(tmp_path)
    s._clock_trusted()
    warned = [e for e in s.events.recent(10) if e.level == "warning"]
    assert warned and "does not know the time" in warned[0].message


def test_it_says_when_the_clock_arrives(monkeypatch, tmp_path):
    answer(monkeypatch, False)
    s = service(tmp_path)
    s._clock_trusted()
    answer(monkeypatch, True)
    assert s._clock_trusted()
    assert any("The clock is set" in e.message for e in s.events.recent(10))


def test_nothing_is_scheduled_while_it_waits(monkeypatch, tmp_path):
    """The guard sits above everything in _tick, including the missed-stage
    check. A Pi that thinks it is Friday must not report Friday night as missed."""
    answer(monkeypatch, False)
    s = service(tmp_path)
    fired_before = dict(s.scheduler.fired.done)

    import asyncio

    asyncio.run(s._tick())

    assert s.scheduler.fired.done == fired_before
    assert not [e for e in s.events.recent(20) if e.kind == "stage"]


def test_a_settled_clock_costs_nothing_per_tick(monkeypatch, tmp_path):
    """Once trusted it never asks again, so the 1 Hz loop stays a 1 Hz loop."""
    answer(monkeypatch, True)
    s = service(tmp_path)
    assert s._clock_trusted()

    def never():
        raise AssertionError("asked again after settling")

    monkeypatch.setattr(clocksync, "synchronised", never)
    assert s._clock_trusted()
