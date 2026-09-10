"""The second real measurement in the project.

Everything the app holds is belief except the plug, because infrared is one-way.
Three probes on the hoses change that: flow is the water the unit circulates,
return is that same water after the bed has had it, and the difference between
them is the heat actually moving.

The rule underneath all of this is the project's oldest one. Never show a value
that has not been confirmed. So a reading that is missing, or old enough to be
history rather than news, is None rather than the last thing we saw.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from hydrosnooze.adapters.probes import FLOW, RETURN, ROOM, STALE_AFTER, Probes, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Health
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 10, 3, 0)


@pytest.fixture
def probes():
    return Probes(VirtualClock(NOW), host="192.0.2.9")


def report(probes: Probes, flow=None, back=None, room=None, at=NOW):
    """A reading arriving, the way _on_state records one: the value, and the fact
    that the board said anything at all."""
    for name, value in ((FLOW, flow), (RETURN, back), (ROOM, room)):
        if value is not None:
            probes.readings[name] = Reading(value, at)
            probes.last_reading_at = at


# --- Never a stale number dressed as a current one ----------------------------


def test_nothing_reported_is_not_a_temperature(probes):
    assert probes.flow_c is None
    assert probes.moving_c is None


def test_a_fresh_reading_comes_back(probes):
    report(probes, flow=24.5)
    assert probes.flow_c == 24.5


def test_an_old_reading_is_not_news(probes):
    """The board reports every 30s, so two minutes is several missed readings.
    Past that the value is not wrong, it is simply not current, and something is
    about to choose a mode on the strength of it."""
    report(probes, flow=24.5, at=NOW - STALE_AFTER - timedelta(seconds=1))
    assert probes.flow_c is None


def test_the_boundary_is_still_current(probes):
    report(probes, flow=24.5, at=NOW - STALE_AFTER + timedelta(seconds=1))
    assert probes.flow_c == 24.5


# --- The number a pad probe could never give ----------------------------------


def test_the_bed_shedding_heat_reads_positive(probes):
    """Return warmer than flow: the bed is putting heat into the water, so it
    wants cooling. This is the signal, and its sign is the useful half."""
    report(probes, flow=19.0, back=22.0)
    assert probes.moving_c == 3.0


def test_the_water_giving_heat_up_reads_negative(probes):
    report(probes, flow=37.0, back=33.0)
    assert probes.moving_c == -4.0


def test_nothing_moving_is_about_zero(probes):
    report(probes, flow=24.0, back=24.05)
    assert abs(probes.moving_c) < 0.3


def test_one_probe_missing_means_no_difference(probes):
    """Half of a subtraction is not a smaller answer, it is no answer."""
    report(probes, flow=19.0)
    assert probes.moving_c is None


def test_a_stale_half_also_means_no_difference(probes):
    report(probes, flow=19.0)
    report(probes, back=22.0, at=NOW - STALE_AFTER - timedelta(seconds=1))
    assert probes.moving_c is None


# --- What arrives from the board ----------------------------------------------


class State:
    def __init__(self, key, value):
        self.key = key
        self.state = value


def test_a_reading_is_stored_against_its_name(probes):
    probes._keys = {7: FLOW}
    probes._on_state(State(7, 24.44))
    assert probes.flow_c == 24.44


def test_a_sensor_we_did_not_ask_for_is_ignored(probes):
    probes._keys = {7: FLOW}
    probes._on_state(State(99, 1234.0))
    assert probes.readings == {}


def test_nan_never_becomes_a_temperature(probes):
    """A bad 1-Wire read arrives as NaN once the filters have done their work.
    Storing it would put a number on screen that is not one."""
    probes._keys = {7: FLOW}
    probes._on_state(State(7, float("nan")))
    assert probes.flow_c is None


def test_a_broken_state_does_not_take_the_subscription_down(probes):
    """This runs on the library's own task. An exception here would kill the
    subscription and be reported nowhere useful."""
    probes._keys = {7: FLOW}
    probes._on_state(object())
    assert probes.readings == {}


# --- The chip -----------------------------------------------------------------


def chip(tmp_path, host="192.0.2.9", **readings):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host=host), echo=False
    )
    service.probes.clock = VirtualClock(NOW)
    service.probes.readings = {
        name: Reading(value, NOW) for name, value in readings.items()
    }
    return next(d for d in service.health() if d.name == "probes")


def test_no_probe_board_says_so_rather_than_showing_a_colour(tmp_path):
    verdict = chip(tmp_path, host="")
    assert verdict.health is Health.SIMULATED
    assert "not measured" in verdict.detail


def test_all_three_reporting_is_green_and_says_which_way(tmp_path):
    verdict = chip(tmp_path, **{FLOW: 19.0, RETURN: 22.0, ROOM: 18.0})
    assert verdict.health is Health.OK
    assert "shedding" in verdict.detail


def test_the_other_direction_reads_the_other_way(tmp_path):
    verdict = chip(tmp_path, **{FLOW: 37.0, RETURN: 33.0, ROOM: 18.0})
    assert "giving" in verdict.detail


def test_two_out_of_three_is_amber_and_names_the_missing_one(tmp_path):
    """Partial failure needs its own state. Rounding it to fine hides a probe
    that has fallen off; rounding it to broken throws away two that work."""
    verdict = chip(tmp_path, **{FLOW: 19.0, ROOM: 18.0})
    assert verdict.health is Health.DEGRADED
    assert "water_return" in verdict.detail
    assert "19.0C" in verdict.detail


def test_nothing_at_all_is_red(tmp_path):
    verdict = chip(tmp_path)
    assert verdict.health is Health.DOWN
    assert "192.0.2.9" in verdict.detail


def test_the_bar_has_five_chips_now(tmp_path):
    service = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    assert [d.name for d in service.health()] == ["plug", "blaster", "probes", "alerts"]


# --- Never allowed to affect a night ------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_probe_board_does_not_stop_a_power_sample(tmp_path):
    """The schedule ran for weeks before these existed. A probe board that has
    gone means the app knows less, not that it stops working."""
    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"), echo=False
    )
    await service._sample_power()
    assert service.state.observed_power_w is not None
    assert service.state.observed_flow_c is None


# --- Noticing that the board has gone quiet -------------------------------------
#
# This adapter never sends anything. It subscribes once and waits, so it has no
# request that could fail and no exception to catch when the socket dies.
# aioesphomeapi does not reconnect on its own and does not say it has stopped:
# the client object carries on existing and looking healthy. The transmitter
# survives that because every press is a real request that raises. Here, silence
# is the only symptom there is, so silence has to be the signal.


@pytest.mark.asyncio
async def test_silence_tears_the_link_down_rather_than_waiting_forever(probes):
    """The bug this replaces: a loop waiting on a flag nothing ever clears.

    Without this the adapter sat on a dead socket at one wakeup a second, for as
    long as the process ran, reporting a connection that had stopped delivering
    hours earlier.
    """
    probes.connected = True
    probes.last_reading_at = probes.clock.now()

    with pytest.raises(TimeoutError, match="rebuilding"):
        await probes._until_it_goes_quiet()

    assert probes.rebuilds == 1


@pytest.mark.asyncio
async def test_a_board_that_keeps_reporting_is_left_alone(probes):
    """A reading resets the clock, so a working link is never torn down."""
    probes.connected = True
    probes.last_reading_at = probes.clock.now()

    async def keep_talking():
        for _ in range(40):
            await probes.clock.sleep(30)
            probes.last_reading_at = probes.clock.now()
        probes.connected = False

    await asyncio.gather(probes._until_it_goes_quiet(), keep_talking())
    assert probes.rebuilds == 0


def test_how_long_it_has_been_quiet_is_asked_for_in_one_place(probes):
    assert probes.quiet_for is None, "nothing has ever arrived"
    report(probes, flow=20.0)
    probes.last_reading_at = probes.clock.now()
    probes.clock.advance(timedelta(minutes=7))
    assert probes.quiet_for == timedelta(minutes=7)


def test_the_red_dot_says_how_long_and_how_often(tmp_path):
    """A red dot is not useful. How long it has been red, and how many times it
    has gone red, are: one says the board has died and the other says the link is
    flapping, and they want opposite fixes."""
    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"), echo=False
    )
    service.probes.clock = VirtualClock(NOW)
    service.probes.last_reading_at = NOW - timedelta(minutes=22)
    service.probes.rebuilds = 4

    verdict = next(d for d in service.health() if d.name == "probes")
    assert verdict.health is Health.DOWN
    assert "22 minutes" in verdict.detail
    assert "4 reconnects" in verdict.detail


@pytest.mark.asyncio
async def test_going_quiet_and_coming_back_are_both_said_out_loud(tmp_path):
    """The plug has always done this and the probes never did. A board that drops
    out every night is a pattern, and a pattern is invisible if the only place it
    shows is a dot that happens to be red when someone looks."""
    # One clock for both. The probes read freshness off theirs and the service
    # measures the gap off its own, and in the running app they are the same
    # object.
    clock = VirtualClock(NOW)
    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=clock,
        echo=False,
    )

    report(service.probes, 20.0, 20.0, 19.0, at=NOW)
    await service._sample_power()
    assert not [e for e in service.events.recent(20) if e.kind == "probes"]

    clock.advance(timedelta(minutes=25))
    await service._sample_power()
    gone = [e for e in service.events.recent(20) if e.kind == "probes"]
    assert len(gone) == 1
    assert "stopped reporting" in gone[0].message

    report(service.probes, 20.0, 20.0, 19.0, at=clock.now())
    await service._sample_power()
    back = [e for e in service.events.recent(20) if e.kind == "probes"]
    assert len(back) == 2
    assert "reporting again" in back[0].message
    assert "25 minutes" in back[0].message


def test_the_probes_never_push_to_a_phone_at_three_in_the_morning(tmp_path):
    """A night does not depend on these. A board on a bedroom Wi-Fi link losing
    its connection must never be worth waking someone for."""
    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"), echo=False
    )
    service._probes_ok = True
    service._watch_probes(NOW)
    said = [e for e in service.events.recent(20) if e.kind == "probes"]
    assert said and all(e.level == "info" for e in said)
