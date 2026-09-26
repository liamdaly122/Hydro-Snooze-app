"""Holding the bed at the number: the Hold level and the night-time trim.

The case that started it: a 32C REM part that spent the small hours at 30,
because the bed went quiet at 31.5 and warmed again only at 30.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from hydrosnooze import hold
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.models import TRIM_KIND, Mode, Power, Schedule, SleepStage, Stage, quieter_mode
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import Service

T0 = datetime.combine(date(2026, 9, 25), time(2, 0))
BEAT = timedelta(seconds=30)


# --- The trim's decision ----------------------------------------------------------------


def steady(minutes: float, bed: float, *, end=T0) -> list[tuple[datetime, float]]:
    n = int(minutes * 2)
    return [(end - BEAT * (n - i), bed) for i in range(n + 1)]


def test_nothing_until_a_full_window():
    assert hold.trim_needed([], 32, T0) == 0
    assert hold.trim_needed(steady(29, 30.6), 32, T0) == 0


def test_a_bed_sat_low_for_the_whole_window_wants_a_degree_more():
    assert hold.trim_needed(steady(30, 30.6), 32, T0) == 1


def test_a_bed_sat_high_wants_a_degree_less():
    assert hold.trim_needed(steady(30, 33.0), 32, T0) == -1


def test_within_half_a_degree_is_left_alone():
    assert hold.trim_needed(steady(30, 31.6), 32, T0) == 0
    assert hold.trim_needed(steady(30, 32.4), 32, T0) == 0


def test_one_reading_near_the_target_is_enough_to_wait():
    readings = steady(30, 30.6)
    readings[10] = (readings[10][0], 31.8)
    assert hold.trim_needed(readings, 32, T0) == 0


def test_a_bed_still_closing_on_the_target_is_left_to_get_there():
    n = 60
    climbing = [(T0 - BEAT * (n - i), 29.0 + 2.0 * i / n) for i in range(n + 1)]
    assert hold.trim_needed(climbing, 32, T0) == 0
    falling = [(T0 - BEAT * (n - i), 35.0 - 2.0 * i / n) for i in range(n + 1)]
    assert hold.trim_needed(falling, 32, T0) == 0


# --- The Hold level, in the drift response -----------------------------------------------


def mode_for(level: str, running: Mode, bed: float) -> Mode | None:
    h = hold.HOLDS[level]
    return quieter_mode(32, running, bed, Mode.QUIET, cap_c=40,
                        arrived_c=h.arrived_c, fallen_c=h.fallen_c)


@pytest.mark.parametrize(("level", "goes_quiet_at", "not_yet_at"), [
    ("quiet", 31.5, 31.4), ("balanced", 32.0, 31.9), ("close", 33.0, 32.9),
])
def test_when_a_warm_part_goes_quiet(level, goes_quiet_at, not_yet_at):
    assert mode_for(level, Mode.WARMING, goes_quiet_at) is Mode.QUIET
    assert mode_for(level, Mode.WARMING, not_yet_at) is None


@pytest.mark.parametrize(("level", "warms_at", "not_yet_at"), [
    ("quiet", 30.0, 30.1), ("balanced", 31.0, 31.1), ("close", 31.5, 31.6),
])
def test_when_it_warms_again(level, warms_at, not_yet_at):
    assert mode_for(level, Mode.QUIET, warms_at) is Mode.WARMING
    assert mode_for(level, Mode.QUIET, not_yet_at) is None


def test_the_old_behaviour_is_the_quiet_level():
    assert quieter_mode(32, Mode.WARMING, 31.5, Mode.QUIET, cap_c=40) is Mode.QUIET
    assert quieter_mode(32, Mode.QUIET, 30.0, Mode.QUIET, cap_c=40) is Mode.WARMING


# --- The service ----------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(T0), echo=False)
    svc.schedule = Schedule(
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DRIFT, 35, 29),
            SleepStage(Stage.DEEP, 222, 27),
            SleepStage(Stage.REM, 195, 32),
            SleepStage(Stage.WAKE, 28, 34),
        ],
    )
    svc._set_state(assumed_mode=Mode.WARMING)
    svc.sent = []

    async def apply(mode, target_c):
        svc.sent.append(svc._corrected(target_c, mode))
        svc._set_state(assumed_mode=mode)

    monkeypatch.setattr(svc, "_apply", apply)
    svc.bed = 30.6
    monkeypatch.setattr(type(svc.probes), "bed_c", property(lambda self: svc.bed))
    yield svc
    svc.db.close()


def rem(svc: Service):
    return svc.schedule.plan_for(date(2026, 9, 25)).steps[2]


def run_for(svc: Service, minutes: float, *, step=None, start=T0) -> datetime:
    step = step or rem(svc)

    async def go():
        t = start
        for _ in range(int(minutes * 2) + 1):
            await svc._trim(step, Power.ON, t)
            t += BEAT
        return t

    return asyncio.run(go())


def test_a_bed_sat_low_for_half_an_hour_is_sent_a_degree_more(service):
    run_for(service, 29)
    assert service.sent == []
    run_for(service, 2, start=T0 + timedelta(minutes=29.5))
    assert service.sent == [33]
    said = [e for e in service.events.recent() if e.kind == TRIM_KIND]
    assert said and "a degree more" in said[-1].message, "filed as a drift response"


def test_one_degree_at_a_time_and_a_full_window_between(service):
    run_for(service, 59)
    assert service.sent == [33]
    run_for(service, 2, start=T0 + timedelta(minutes=59.5))
    assert service.sent == [33, 34]


def test_a_bed_sat_high_is_sent_a_degree_less(service):
    service.bed = 33.0
    run_for(service, 31)
    assert service.sent == [31]


def test_never_past_four_degrees_counting_the_learned_correction(service, monkeypatch):
    monkeypatch.setattr(service.db, "learned_offset_c", lambda mode, c: -2.1)
    run_for(service, 200)
    assert service.sent == [35, 36], "learned +2, then +1 and +1, and no further"
    said = [e.message for e in service.events.recent() if "as far as the setting may go" in e.message]
    assert len(said) == 1, "said once a part"


def test_never_past_the_safety_cap(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "c.db"), max_temperature_c=33),
                  clock=VirtualClock(T0), echo=False)
    svc.schedule = Schedule(days_of_week=list(range(7)), stages=[
        SleepStage(Stage.DEEP, 240, 27), SleepStage(Stage.REM, 240, 32)])
    svc._set_state(assumed_mode=Mode.WARMING)
    sent = []

    async def apply(mode, target_c):
        sent.append(svc._corrected(target_c, mode))

    monkeypatch.setattr(svc, "_apply", apply)
    monkeypatch.setattr(type(svc.probes), "bed_c", property(lambda self: 30.0))
    step = next(st for st in svc.schedule.plan_for(date(2026, 9, 25)).steps
                if st.stage is Stage.REM)

    async def go():
        t = T0
        for _ in range(240):
            await svc._trim(step, Power.ON, t)
            t += BEAT

    asyncio.run(go())
    assert sent == [33]
    svc.db.close()


def test_it_starts_again_at_every_part(service):
    run_for(service, 31)
    assert service._trim_for(32, Mode.WARMING) == 1
    wake = service.schedule.plan_for(date(2026, 9, 25)).steps[3]
    run_for(service, 1, step=wake, start=T0 + timedelta(minutes=32))
    assert service._trims == {} and service._trim_for(32, Mode.WARMING) == 0


def test_nothing_trims_with_autopilot_off(service):
    asyncio.run(service.set_autopilot(False))
    run_for(service, 61)
    assert service.sent == [] and service._trim_for(32, Mode.WARMING) == 0


def test_a_nudge_pauses_it(service, monkeypatch):
    monkeypatch.setattr(service, "tonight_state", lambda: SimpleNamespace(nudge_at=lambda now: -1))
    run_for(service, 61)
    assert service.sent == []


def test_the_hold_level_decides_when_a_warm_part_goes_quiet(service, monkeypatch):
    applied = []

    async def apply(mode, target_c):
        applied.append(mode)

    monkeypatch.setattr(service, "_apply", apply)
    service.bed = 31.6
    step = rem(service)

    asyncio.run(service._correct_mode(step, Power.ON, T0))
    assert applied == [], "balanced: not quiet until the bed reaches 32"

    service.set_hold("quiet")
    service._mode_changed_at = None
    asyncio.run(service._correct_mode(step, Power.ON, T0))
    assert applied == [Mode.QUIET], "quiet: half a degree short is close enough"


def test_the_hold_level_is_kept_and_checked(service):
    assert service.autopilot_state()["hold"] == "balanced"
    assert service.set_hold("close")["hold"] == "close"
    assert service.db.hold() == "close"
    with pytest.raises(CommandFailed):
        service.set_hold("loud")


def test_a_database_from_before_the_choice_is_balanced(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "learning_on INTEGER NOT NULL DEFAULT 1, timing_since TEXT, "
        "autopilot_on INTEGER NOT NULL DEFAULT 1)"
    )
    old.execute("INSERT INTO preferences (id) VALUES (1)")
    old.commit()
    old.close()
    db = Database(path)
    try:
        assert db.hold() == "balanced"
    finally:
        db.close()


def test_the_morning_report_counts_trims_apart_from_mode_swaps():
    """And only the trims that went through. One that failed is an error, and
    the report already says so under what went wrong."""
    from hydrosnooze import report
    from hydrosnooze.events import Event
    from hydrosnooze.models import QUIET_KIND

    plan = Schedule(days_of_week=list(range(7))).plan_for(date(2026, 9, 25))
    at = plan.steps[1].starts_at + timedelta(hours=1)
    events = [
        Event(0, at, "info", QUIET_KIND, "switched to quiet"),
        Event(0, at, "info", TRIM_KIND, "a degree more"),
        Event(0, at, "info", TRIM_KIND, "a degree more"),
        Event(0, at, "error", TRIM_KIND, "Could not send temp_up"),
    ]
    body = report.build(plan, [], events, set(), None).body
    assert "Swapped mode once to keep it quiet." in body
    assert "Trimmed the setting twice to hold the number." in body
