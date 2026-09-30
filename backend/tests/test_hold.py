"""Holding the bed at the number: Stay on target and the night-time trim.

The case that started it: a 32C REM part that spent the small hours at 30,
because the bed went quiet at 31.5 and warmed again only at 30. Then three Hold
levels that on the real bed did not change much, and one switch instead: every
part stays in the mode it started in.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from hydrosnooze import hold
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.models import TRIM_KIND, Mode, Power, Schedule, SleepStage, Stage, quieter_mode
from hydrosnooze.service import HOLD_KIND, Service

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


# --- Stay on target off: where a warm part swaps -------------------------------------------


def mode_for(running: Mode, bed: float) -> Mode | None:
    return quieter_mode(32, running, bed, Mode.QUIET, cap_c=40,
                        arrived_c=hold.QUIET_AT_C, fallen_c=hold.WARM_AGAIN_C)


def test_off_a_warm_part_goes_quiet_at_the_number():
    """What the Balanced level did."""
    assert mode_for(Mode.WARMING, 32.0) is Mode.QUIET
    assert mode_for(Mode.WARMING, 31.9) is None


def test_off_it_warms_again_a_degree_below():
    assert mode_for(Mode.QUIET, 31.0) is Mode.WARMING
    assert mode_for(Mode.QUIET, 31.1) is None


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


def correct(svc: Service, step, now=T0) -> list[Mode]:
    applied: list[Mode] = []

    async def apply(mode, target_c):
        applied.append(mode)
        svc._set_state(assumed_mode=mode, assumed_target_c=target_c)

    svc._apply = apply  # type: ignore[method-assign]
    asyncio.run(svc._correct_mode(step, Power.ON, now))
    return applied


def test_off_a_warm_part_at_the_number_goes_quiet(service):
    service.bed = 32.0
    assert correct(service, rem(service)) == [Mode.QUIET]


def test_on_a_warm_part_at_the_number_keeps_warming(service):
    service.set_stay_on_target(True)
    service.bed = 33.5
    assert correct(service, rem(service)) == [], "never quiet, however warm the bed"


def test_on_a_cooling_part_below_the_number_keeps_cooling(service):
    """The other swap goes too: a part stays in the mode it started in."""
    service.set_stay_on_target(True)
    service._set_state(assumed_mode=Mode.QUIET)
    service.bed = 29.0
    assert correct(service, rem(service)) == []


def test_on_the_trim_still_holds_the_number(service):
    """Staying in one mode is what gives the trim its full half hour."""
    service.set_stay_on_target(True)
    run_for(service, 31)
    assert service.sent == [33]


def follow(svc: Service, step, now=T0) -> list[tuple[Mode, int]]:
    applied: list[tuple[Mode, int]] = []

    async def apply(mode, target_c):
        applied.append((mode, target_c))
        svc._set_state(assumed_mode=mode, assumed_target_c=target_c)

    svc._apply = apply  # type: ignore[method-assign]
    asyncio.run(svc._follow_the_plan(step, Power.ON, now))
    return applied


def test_turned_on_after_a_swap_it_goes_back_to_the_plans_mode(service):
    step = rem(service)
    assert step.mode is Mode.WARMING
    # Off, the part went quiet at the number, and following the plan keeps that.
    service._set_state(assumed_mode=Mode.QUIET, assumed_target_c=32)
    assert follow(service, step) == []

    service._followed_at = T0
    service.set_stay_on_target(True)
    assert follow(service, step, T0 + BEAT) == [(Mode.WARMING, 32)], "straight away"
    assert follow(service, step, T0 + 2 * BEAT) == [], "and once there, silence"


def test_on_a_press_part_way_moves_it_the_way_the_number_moved(service):
    """Cooler at 3am in a warm part is the number going down, so it cools, and
    stays cooling. Warmer, or the press running out, is up again, so it warms."""
    service.set_stay_on_target(True)
    step = rem(service)
    service._set_state(assumed_mode=Mode.WARMING, assumed_target_c=32)
    assert follow(service, step) == []

    cooler = replace(step, temp_c=30)
    assert cooler.mode is Mode.WARMING, "the plan still says warming: up from Deep"
    assert follow(service, cooler, T0 + BEAT) == [(Mode.QUIET, 30)]
    later = T0 + timedelta(minutes=31)
    assert follow(service, cooler, later) == [], "and stays cooling"
    assert follow(service, step, later + timedelta(minutes=31)) == [(Mode.WARMING, 32)]


def test_on_does_nothing_with_autopilot_off(service):
    service.set_stay_on_target(True)
    asyncio.run(service.set_autopilot(False))
    service._set_state(assumed_mode=Mode.QUIET, assumed_target_c=32)
    service._followed_at = None
    assert follow(service, rem(service)) == [], "off runs the night as it always has"
    assert service.autopilot_state() == {"on": False, "stay_on_target": True}


def test_it_is_kept_and_said(service):
    assert service.autopilot_state()["stay_on_target"] is False
    assert service.set_stay_on_target(True)["stay_on_target"] is True
    assert service.db.stay_on_target() is True
    said = [e.message for e in service.events.recent() if e.kind == HOLD_KIND]
    assert said and said[-1].startswith("Stay on target on.")
    assert service.set_stay_on_target(False)["stay_on_target"] is False


def old_database(tmp_path, hold_value: str | None) -> Database:
    path = tmp_path / f"old-{hold_value}.db"
    old = sqlite3.connect(path)
    extra = ", hold TEXT NOT NULL DEFAULT 'balanced'" if hold_value else ""
    old.execute(
        "CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "learning_on INTEGER NOT NULL DEFAULT 1, timing_since TEXT, "
        f"autopilot_on INTEGER NOT NULL DEFAULT 1{extra})"
    )
    if hold_value:
        old.execute("INSERT INTO preferences (id, hold) VALUES (1, ?)", (hold_value,))
    else:
        old.execute("INSERT INTO preferences (id) VALUES (1)")
    old.commit()
    old.close()
    return Database(path)


@pytest.mark.parametrize(("was", "on"), [
    (None, False), ("quiet", False), ("balanced", False), ("close", True),
])
def test_an_older_database_carries_over(tmp_path, was, on):
    """Close already kept warm parts warming, so it is on. The rest swapped."""
    db = old_database(tmp_path, was)
    try:
        assert db.stay_on_target() is on
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
