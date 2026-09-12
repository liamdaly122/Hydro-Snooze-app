"""Asking for a 30 degree bed and getting one.

Liam noticed the probes reading about two degrees below whatever the app set,
and asked whether the app should add two. The measurement said the number is real
and a flat two would have been wrong, which is why this is learned rather than
picked.

From `./scripts/calibration.py` on the real Pi, 12 September, both runs decided by
the probes with nobody in the bed:

    warming   asked 28   bed 25.9   off by -2.1   room 20.6
    turbo     asked 27   bed 26.6   off by -0.4   room 20.5

Almost the same target in almost the same room, and one droops five times further
than the other. That rules out a single constant. The unit heats water at its own
outlet and the probes sit on the hose at the bed, so heat leaks in between, and
how much depends on how hard the mode circulates.

The correction is invisible on purpose: the number on the screen is the bed, and
this is how it gets there.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import MIN_RUNS_TO_LEARN, Database
from hydrosnooze.models import Mode, Schedule, SleepStage, Stage
from hydrosnooze.service import CORRECTION_LIMIT_C, Service

NOW = datetime(2026, 9, 12, 20, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def ran(db, mode: str, target: int, bed: float, *, room: float = 20.5) -> None:
    """One finished run, the way the probes record it."""
    db.record_precondition(
        NOW, mode, target, 1500, True, start_c=21.0, end_c=bed, room_c=room, decided_by="probes"
    )


# --- What the runs teach ----------------------------------------------------------


def test_one_night_does_not_earn_a_correction(db):
    """No correction is better than a confident wrong one."""
    ran(db, "warming", 28, 25.9)
    assert db.learned_offset_c("warming", 28) is None


def test_a_few_nights_give_the_measured_droop(db):
    for bed in (25.9, 26.1, 25.8):
        ran(db, "warming", 28, bed)
    assert db.learned_offset_c("warming", 28) == -2.1


def test_the_two_modes_are_learned_apart(db):
    """The finding that ruled out one flat correction. Almost the same target in
    almost the same room, and warming droops five times further than turbo."""
    for bed in (25.9, 26.1, 25.8):
        ran(db, "warming", 28, bed)
    for bed in (26.6, 26.7, 26.5):
        ran(db, "turbo", 27, bed)

    assert db.learned_offset_c("warming", 28) == -2.1
    assert db.learned_offset_c("turbo", 27) == -0.4


def test_a_distant_target_is_not_evidence_for_this_one(db):
    for bed in (25.9, 26.1, 25.8):
        ran(db, "warming", 28, bed)
    assert db.learned_offset_c("warming", 40) is None


def test_a_run_that_never_got_there_teaches_nothing(db):
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        db.record_precondition(NOW, "warming", 28, 9999, False, end_c=25.9)
    assert db.learned_offset_c("warming", 28) is None


def test_a_plug_only_run_teaches_nothing(db):
    """The plug times the machine. Only the probes measure the bed, and without a
    bed reading there is no gap to learn."""
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        db.record_precondition(NOW, "warming", 28, 1500, True, decided_by="plug")
    assert db.learned_offset_c("warming", 28) is None


# --- What the service does with it -------------------------------------------------


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 28),
        ],
    )
    svc.load_tonight()
    yield svc
    svc.db.close()


def taught(service, mode: str, target: int, bed: float) -> None:
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(service.db, mode, target, bed)


def test_with_nothing_measured_it_sends_what_was_asked(service):
    assert service._corrected(28, Mode.WARMING) == 28


def test_it_sends_the_other_way_from_the_droop(service):
    """The bed lands 2.1 low, so the unit is asked for 2 high."""
    taught(service, "warming", 28, 25.9)
    assert service._corrected(28, Mode.WARMING) == 30


def test_a_droop_too_small_to_matter_is_left_alone(service):
    """Under half a degree is inside what the two probes disagree about on a
    settled bed. Correcting it would be measuring the probes."""
    taught(service, "turbo", 27, 26.7)
    assert service._corrected(27, Mode.TURBO) == 27


def test_the_correction_cannot_run_away(service):
    """A heater under a mattress is the one thing here worth a hard limit rather
    than a warning."""
    taught(service, "warming", 30, 10.0)
    assert service._corrected(30, Mode.WARMING) == 30 + CORRECTION_LIMIT_C


def test_it_never_sends_past_the_safety_cap(service):
    cap = service.settings.max_temperature_c
    taught(service, "warming", cap, cap - 3.0)
    assert service._corrected(cap, Mode.WARMING) <= cap


def test_it_says_once_a_night_that_the_two_numbers_differ(service):
    """Worth knowing that what is being sent is not what is on the screen. Not
    worth saying four times before breakfast."""
    taught(service, "warming", 28, 25.9)
    for _ in range(4):
        service._corrected(28, Mode.WARMING)

    said = [e.message for e in service.events.recent(20) if "to get a" in e.message]
    assert len(said) == 1
    assert "Sending 30C to get a 28C bed" in said[0]
    assert "measured on recent nights" in said[0]


# --- The script that reports all this ---------------------------------------------


def test_the_script_uses_the_same_rules_as_the_service():
    """`scripts/calibration.py` keeps its own copies of these so it can run on any
    machine with nothing but Python: no virtualenv, no package, just the database.

    That is worth having and it is a drift risk, so this is the thing that fails
    when somebody changes one and not the other. A script that reports "2 more
    nights" against a service that wants three is worse than no script.
    """
    import re
    from pathlib import Path

    from hydrosnooze import db as real

    source = (Path(__file__).resolve().parents[2] / "scripts" / "calibration.py").read_text()

    def declared(name: str) -> float:
        found = re.search(rf"^{name} = ([\d.]+)$", source, re.M)
        assert found, f"{name} is not declared in the script any more"
        return float(found.group(1))

    assert declared("MIN_RUNS_TO_LEARN") == real.MIN_RUNS_TO_LEARN
    assert declared("MIN_LEARNABLE_GAP_C") == real.MIN_LEARNABLE_GAP_C
    assert declared("LEARNED_BASE_MINUTES") == real.LEARNED_BASE_MINUTES
