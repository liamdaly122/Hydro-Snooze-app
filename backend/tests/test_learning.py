"""Learning how long this bed really takes, instead of estimating it.

The lead time before bedtime was a fixed cost plus a rate per degree, from an
assumed room temperature. Every number in it was a guess. Two sensors can answer
it properly, and they answer it differently.

The plug answers it about the machine: a unit working towards a setpoint draws
170 W cooling or 300 W heating, and when it arrives the draw falls into the idle
band. That was the whole answer for months.

The hose probes answer it about the bed, which is the better question. While the
bed is still taking heat the water comes back at a different temperature from the
way it went out, and when that gap closes the exchange has finished.

The probes lead and the plug backs them up, so a probe board that has fallen off
the Wi-Fi costs accuracy and never costs a night.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from hydrosnooze.adapters.probes import FLOW, RETURN, ROOM, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import MIN_RUNS_TO_LEARN, Database
from hydrosnooze.models import Mode, preconditioning_for
from hydrosnooze.service import MIN_PRECONDITION_SECONDS, PreconditionRun, Service

NOW = datetime(2026, 9, 8, 21, 0)


# --- What the plug is asked to say ---------------------------------------------


@pytest.fixture
def db():
    return Database(":memory:")


def ran(db, *, target=17, mode="turbo", minutes=30, frm=27.0, to=17.0, reached=True):
    """One finished run: how long it took and how far it actually went."""
    db.record_precondition(
        NOW, mode, target, minutes * 60, reached, start_c=frm, end_c=to
    )


def test_one_night_is_not_enough_to_learn_from(db):
    """An anecdote should not replace a consistent estimate."""
    ran(db)
    assert db.learned_lead_minutes("turbo", 17, 10.0) is None


def test_a_few_nights_give_a_rate_and_apply_it_to_tonight(db):
    """Thirty minutes to cover ten degrees is three minutes a degree. What that
    is worth tonight depends entirely on how far tonight has to go."""
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db, minutes=30, frm=27.0, to=17.0)

    assert db.learned_lead_minutes("turbo", 17, 10.0) == 35, "the same trip again"
    assert db.learned_lead_minutes("turbo", 17, 2.0) == 11, "a shorter one costs less"
    assert db.learned_lead_minutes("turbo", 17, 20.0) == 65, "and a longer one more"


def test_a_run_that_barely_moved_teaches_nothing(db):
    """The bug of 11 September. Liam had spent a week with the bed already warm,
    so its runs had half a degree to cover and got there almost at once. Three of
    those taught it that this bed warms in two minutes, as a fact about the bed
    rather than about the half degree, and the pre-heat went out at 21:58 for a
    22:00 bedtime aiming to move it eight degrees."""
    for _ in range(MIN_RUNS_TO_LEARN + 2):
        ran(db, mode="warming", target=28, minutes=2, frm=27.6, to=28.1)

    assert db.learned_lead_minutes("warming", 28, 8.0) is None, "two minutes is not a rate"


def test_runs_that_never_got_there_do_not_count(db):
    """A night the unit could not reach the target says nothing about how long
    reaching it takes."""
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        ran(db, minutes=166, reached=False)
    assert db.learned_lead_minutes("turbo", 17, 10.0) is None


def test_a_run_with_no_probe_readings_cannot_teach_a_rate(db):
    """Distance is what makes a duration mean anything, and a run decided off the
    plug alone never recorded one. The estimate is better than a number that
    silently assumes tonight is the same trip as last night."""
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        db.record_precondition(NOW, "turbo", 17, 1800, True)
    assert db.learned_lead_minutes("turbo", 17, 10.0) is None


def test_each_mode_is_learned_separately(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    assert db.learned_lead_minutes("warming", 17, 10.0) is None


def test_a_distant_target_is_not_evidence_for_this_one(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    assert db.learned_lead_minutes("turbo", 30, 10.0) is None
    assert db.learned_lead_minutes("turbo", 18, 10.0) == 35, "nearby targets still count"


# --- What the plan does with it -------------------------------------------------


def test_without_history_it_estimates_and_says_so():
    pre = preconditioning_for(17, Mode.QUIET)
    assert pre.runs
    assert "Estimated" in pre.reason


def test_with_history_it_uses_the_measurement_and_says_so():
    pre = preconditioning_for(17, Mode.QUIET, learned=lambda mode, target, gap: 42)
    assert pre.lead_minutes == 42
    assert "measured on recent nights" in pre.reason


def test_a_measured_lead_still_respects_the_cap():
    """Something has gone wrong if it measures four hours, and a lead longer than
    the cap would have the unit running most of the evening."""
    pre = preconditioning_for(17, Mode.QUIET, learned=lambda mode, target, gap: 10_000)
    assert pre.lead_minutes < 10_000


def test_the_lookup_is_asked_about_the_mode_that_was_chosen():
    """The mode is decided inside, so the lookup cannot be done before the call.
    A warming night must not be timed with cooling's history."""
    asked: list[Mode] = []

    def learned(mode: Mode, target: int, gap: float) -> int | None:
        asked.append(mode)
        return None

    preconditioning_for(30, Mode.QUIET, learned=learned)
    assert asked == [Mode.WARMING]


# --- The service closing the loop ------------------------------------------------


@pytest.fixture
def service():
    clock = VirtualClock(NOW)
    return Service(Settings(db_path=":memory:"), clock=clock, echo=False)


def start(service, target=17, mode=Mode.TURBO, start_c=None):
    service._precondition = PreconditionRun(
        started=service.clock.now(), mode=mode, target_c=target, start_c=start_c
    )


def settle(service, watts: float, after: timedelta):
    service.clock.advance(after)
    service._watch_precondition(watts, service.clock.now())


def hoses(service, flow: float, back: float, room: float | None = None):
    """Put a reading on each hose probe, timestamped now so it counts as current."""
    at = service.clock.now()
    for name, value in ((FLOW, flow), (RETURN, back), (ROOM, room)):
        if value is not None:
            service.probes.readings[name] = Reading(value, at)


def step(service, after: timedelta, *, watts=None, flow=None, back=None, room=None):
    """One beat of the sampling loop, with whatever each sensor is saying.

    Readings are stamped after the clock moves, because a probe reading goes
    stale in two minutes and these steps are longer than that. Leaving flow out
    is a probe board that is not reporting.
    """
    service.clock.advance(after)
    if flow is not None:
        hoses(service, flow, back, room)
    service._watch_precondition(watts, service.clock.now())


def test_the_draw_falling_to_idle_is_what_records_the_run(service):
    start(service)
    settle(service, 170.0, timedelta(minutes=10))
    assert service.db.precondition_runs() == [], "still working, nothing to record yet"

    settle(service, 7.0, timedelta(minutes=15))
    runs = service.db.precondition_runs()
    assert len(runs) == 1
    assert (runs[0].mode, runs[0].target_c, runs[0].reached) == ("turbo", 17, True)
    assert runs[0].seconds == 25 * 60
    assert runs[0].decided_by == "plug", "no probes reporting, so the plug decided"


def test_an_early_idle_reading_is_the_unit_not_started_yet(service):
    """Power on, mode change and rail-and-count take about thirty seconds of
    infrared before the compressor does anything, and the plug reads idle
    throughout. Believing that would learn a lead time of zero."""
    start(service)
    settle(service, 6.0, timedelta(seconds=MIN_PRECONDITION_SECONDS - 10))
    assert service.db.precondition_runs() == []


def test_a_run_that_never_settles_is_recorded_as_not_reached(service):
    """More useful than the timing would have been: it means the target is not
    achievable in this room."""
    start(service, target=15)
    settle(service, 180.0, timedelta(hours=4))
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].reached is False
    assert any("not be reachable" in e.message for e in service.events.recent(20))


def test_nothing_is_recorded_when_no_run_is_in_flight(service):
    settle(service, 6.0, timedelta(minutes=30))
    assert service.db.precondition_runs() == []


def test_an_unreachable_plug_does_not_end_a_run(service):
    """None is "we could not ask", not "it arrived"."""
    start(service)
    settle(service, None, timedelta(minutes=30))
    assert service.db.precondition_runs() == []
    assert service._precondition is not None


# --- The probes leading, and the plug behind them --------------------------------

# The numbers below are the ones the hoses really read on the evening the probes
# went on. Heating hard: 34.56C out, 33.00C back. An hour later, sat at
# temperature: 41.31C out, 41.06C back.


def test_the_gap_on_the_hoses_closing_is_what_records_the_run(service):
    """The bed itself saying it is ready, rather than the machine saying it stopped."""
    start(service, start_c=19.2)
    step(service, timedelta(minutes=10), watts=305.0, flow=34.56, back=33.00, room=19.7)
    assert service.db.precondition_runs() == [], "the water is still coming back changed"

    step(service, timedelta(minutes=15), watts=305.0, flow=41.31, back=41.06, room=19.7)
    runs = service.db.precondition_runs()
    assert len(runs) == 1
    assert runs[0].decided_by == "probes"
    assert runs[0].reached is True
    assert runs[0].seconds == 25 * 60


def test_the_probes_decide_even_while_the_unit_is_still_drawing(service):
    """This is the whole reason they lead.

    The plug can only see the unit stop. The bed is ready before that, and on a
    warm night the unit may never fall to idle at all.
    """
    start(service)
    step(service, timedelta(minutes=10), watts=305.0, flow=34.56, back=33.00)
    step(service, timedelta(minutes=10), watts=305.0, flow=41.31, back=41.06)
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].decided_by == "probes"


def test_a_gap_that_was_never_open_is_the_unit_not_started_yet(service):
    """The guard the probe rule needs, and the one the plug rule got for free.

    Before circulation starts, the two hoses sit at the same temperature and read
    exactly like a bed that has finished. Believing that would record a run of two
    minutes and learn a lead time of nothing.
    """
    start(service)
    for _ in range(6):
        step(service, timedelta(minutes=5), watts=305.0, flow=30.0, back=30.0)
    assert service.db.precondition_runs() == []


def test_the_plug_still_decides_when_the_probes_are_quiet(service):
    """The board is new and on a bedroom Wi-Fi link. A night cannot depend on it."""
    start(service)
    step(service, timedelta(minutes=10), watts=305.0)
    step(service, timedelta(minutes=15), watts=7.0)
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].decided_by == "plug"


def test_a_probe_board_that_missed_the_working_phase_hands_back_to_the_plug(service):
    """The probes came back to a closed gap they never saw open.

    That is indistinguishable from a unit which never started, so they say
    nothing and the plug answers instead. This is what having two sensors is for:
    the answer is less precise, and there still is one.
    """
    start(service)
    step(service, timedelta(minutes=5), watts=305.0)  # board off the Wi-Fi
    step(service, timedelta(minutes=20), watts=7.0, flow=41.31, back=41.06)
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].decided_by == "plug"


def test_a_bed_already_at_temperature_falls_back_to_the_plug(service):
    """Probes reporting, but nothing to report: the gap never opens because there
    is no work to do. The plug is the one that can tell the difference between
    that and a unit that has not started."""
    start(service)
    step(service, timedelta(minutes=5), watts=6.0, flow=24.0, back=24.0)
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].decided_by == "plug"


def test_which_probe_is_on_which_hose_does_not_change_the_answer(service):
    """Cooling puts the warmer water on the return; heating puts it on the flow.

    Which of the two a given probe ended up on was decided with a roll of tape
    behind a bed, so the rule reads the size of the gap and never its sign.
    """
    start(service, mode=Mode.QUIET, target=24)
    step(service, timedelta(minutes=10), watts=175.0, flow=22.0, back=23.6)
    assert service.db.precondition_runs() == []

    step(service, timedelta(minutes=10), watts=175.0, flow=23.9, back=24.1)
    runs = service.db.precondition_runs()
    assert len(runs) == 1 and runs[0].decided_by == "probes"


def test_what_the_bed_was_and_what_it_became_are_both_recorded(service):
    """The timing alone never said how far it had to go."""
    start(service, start_c=19.2)
    step(service, timedelta(minutes=10), watts=305.0, flow=34.56, back=33.00, room=19.7)
    step(service, timedelta(minutes=15), watts=305.0, flow=41.31, back=41.06, room=19.6)
    row = service.db.precondition_runs()[0]
    assert row.start_c == 19.2
    assert row.end_c == 41.06, "the return hose, because that water has been through the bed"
    assert row.room_c == 19.6


def test_neither_sensor_answering_records_nothing_at_all(service):
    """Not a failure. A guess in this table becomes a wrong lead time for weeks."""
    start(service)
    step(service, timedelta(hours=4), watts=None)
    assert service.db.precondition_runs() == []
    assert service._precondition is not None
