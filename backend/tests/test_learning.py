"""Learning how long this bed really takes, instead of estimating it.

The lead time before bedtime was a fixed cost plus a rate per degree, from an
assumed room temperature. Every number in it was a guess. The plug can answer it
properly: a unit working towards a setpoint draws 170 W cooling or 300 W heating,
and when it arrives the draw falls into the idle band. The time to that fall is
the real answer for this bed in this room.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import MIN_RUNS_TO_LEARN, Database
from hydrosnooze.models import Mode, preconditioning_for
from hydrosnooze.service import MIN_PRECONDITION_SECONDS, Service

NOW = datetime(2026, 9, 8, 21, 0)


# --- What the plug is asked to say ---------------------------------------------


@pytest.fixture
def db():
    return Database(":memory:")


def test_one_night_is_not_enough_to_learn_from(db):
    """An anecdote should not replace a consistent estimate."""
    db.record_precondition(NOW, "turbo", 17, 1800, True)
    assert db.learned_lead_minutes("turbo", 17) is None


def test_a_few_nights_give_a_real_number(db):
    for seconds in (1800, 2100, 1500):
        db.record_precondition(NOW, "turbo", 17, seconds, True)
    assert db.learned_lead_minutes("turbo", 17) == 30


def test_runs_that_never_got_there_do_not_count(db):
    """A night the unit could not reach the target says nothing about how long
    reaching it takes."""
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        db.record_precondition(NOW, "turbo", 17, 9999, False)
    assert db.learned_lead_minutes("turbo", 17) is None


def test_each_mode_is_learned_separately(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        db.record_precondition(NOW, "turbo", 17, 1800, True)
    assert db.learned_lead_minutes("warming", 17) is None


def test_a_distant_target_is_not_evidence_for_this_one(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        db.record_precondition(NOW, "turbo", 17, 1800, True)
    assert db.learned_lead_minutes("turbo", 30) is None
    assert db.learned_lead_minutes("turbo", 18) == 30, "nearby targets should still count"


# --- What the plan does with it -------------------------------------------------


def test_without_history_it_estimates_and_says_so():
    pre = preconditioning_for(17, Mode.QUIET)
    assert pre.runs
    assert "Estimated" in pre.reason


def test_with_history_it_uses_the_measurement_and_says_so():
    pre = preconditioning_for(17, Mode.QUIET, learned=lambda mode, target: 42)
    assert pre.lead_minutes == 42
    assert "Measured" in pre.reason


def test_a_measured_lead_still_respects_the_cap():
    """Something has gone wrong if it measures four hours, and a lead longer than
    the cap would have the unit running most of the evening."""
    pre = preconditioning_for(17, Mode.QUIET, learned=lambda mode, target: 10_000)
    assert pre.lead_minutes < 10_000


def test_the_lookup_is_asked_about_the_mode_that_was_chosen():
    """The mode is decided inside, so the lookup cannot be done before the call.
    A warming night must not be timed with cooling's history."""
    asked: list[Mode] = []

    def learned(mode: Mode, target: int) -> int | None:
        asked.append(mode)
        return None

    preconditioning_for(30, Mode.QUIET, learned=learned)
    assert asked == [Mode.WARMING]


# --- The service closing the loop ------------------------------------------------


@pytest.fixture
def service():
    clock = VirtualClock(NOW)
    return Service(Settings(db_path=":memory:"), clock=clock, echo=False)


def settle(service, watts: float, after: timedelta):
    service.clock.advance(after)
    service._watch_precondition(watts, service.clock.now())


def test_the_draw_falling_to_idle_is_what_records_the_run(service):
    service._precondition = (NOW, Mode.TURBO, 17)
    settle(service, 170.0, timedelta(minutes=10))
    assert service.db.precondition_runs() == [], "still working, nothing to record yet"

    settle(service, 7.0, timedelta(minutes=15))
    runs = service.db.precondition_runs()
    assert len(runs) == 1
    _at, mode, target, seconds, reached = runs[0]
    assert (mode, target, reached) == ("turbo", 17, True)
    assert seconds == 25 * 60


def test_an_early_idle_reading_is_the_unit_not_started_yet(service):
    """Power on, mode change and rail-and-count take about thirty seconds of
    infrared before the compressor does anything, and the plug reads idle
    throughout. Believing that would learn a lead time of zero."""
    service._precondition = (NOW, Mode.TURBO, 17)
    settle(service, 6.0, timedelta(seconds=MIN_PRECONDITION_SECONDS - 10))
    assert service.db.precondition_runs() == []


def test_a_run_that_never_settles_is_recorded_as_not_reached(service):
    """More useful than the timing would have been: it means the target is not
    achievable in this room."""
    service._precondition = (NOW, Mode.TURBO, 15)
    settle(service, 180.0, timedelta(hours=4))
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0][4] is False
    assert any("not be reachable" in e.message for e in service.events.recent(20))


def test_nothing_is_recorded_when_no_run_is_in_flight(service):
    settle(service, 6.0, timedelta(minutes=30))
    assert service.db.precondition_runs() == []


def test_an_unreachable_plug_does_not_end_a_run(service):
    """None is "we could not ask", not "it arrived"."""
    service._precondition = (NOW, Mode.TURBO, 17)
    settle(service, None, timedelta(minutes=30))
    assert service.db.precondition_runs() == []
    assert service._precondition is not None
