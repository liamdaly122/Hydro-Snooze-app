"""What each night ran, for the scoreboard.

Each part's setting is read from what was recorded across it, never from the
schedule, and the three ways a part stops counting are worked out here: nothing
asked for, a setting held for under half of it, and a hand on the controls.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hydrosnooze import trials
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import Database, Sample
from hydrosnooze.models import Schedule, SleepStage, Stage
from hydrosnooze.service import Service
from hydrosnooze.withings import parse

WAKE = date(2026, 9, 24)
LONDON = ZoneInfo("Europe/London")


def schedule(**temps: int) -> Schedule:
    """23:30 to 07:30, on every morning: Drift 30m, Deep 4h, REM 3h, Wake 30m."""
    t = {"drift": 19, "deep": 17, "rem": 20, "wake": 26, **temps}
    return Schedule(
        bed_time=time(23, 30),
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DRIFT, 30, t["drift"]),
            SleepStage(Stage.DEEP, 240, t["deep"]),
            SleepStage(Stage.REM, 180, t["rem"]),
            SleepStage(Stage.WAKE, 30, t["wake"]),
        ],
    )


@pytest.fixture
def plan():
    return schedule().plan_for(WAKE)


def readings(plan, *, asked=None, room=19.0, bed=0.5, every=timedelta(minutes=1),
             before=timedelta(0)) -> list[Sample]:
    """A reading every minute from `before` ahead of lights out to the wake time.

    `asked(at, step)` says what was being asked for; by default whatever the
    plan's part is set to. The bed sits `bed` above it, on the return hose.
    """
    out = []
    at = plan.bedtime_at - before
    while at < plan.wake_at:
        step = next((s for s in plan.steps if s.starts_at <= at < s.ends_at), plan.steps[0])
        target = asked(at, step) if asked else step.temp_c
        out.append(Sample(
            at=at, watts=170.0,
            return_c=None if target is None else target + bed,
            room_c=room, target_c=target,
        ))
        at += every
    return out


# --- One night ------------------------------------------------------------------------


def test_each_part_is_what_was_asked_for_through_it(plan):
    run = trials.build_run(plan, readings(plan), [])
    assert run.wake_on == WAKE.isoformat()
    assert [(p.part, p.set_c, p.held) for p in run.parts] == [
        ("drift", 19, 1.0), ("deep", 17, 1.0), ("rem", 20, 1.0), ("wake", 26, 1.0),
    ]
    assert [p.bed_c for p in run.parts] == [19.5, 17.5, 20.5, 26.5]
    assert all(p.counts for p in run.parts)
    assert run.room_c == 19.0 and run.rebuilt is False and run.test_part is None


def test_the_setting_is_read_from_the_record_not_the_schedule(plan):
    """A tonight-only change, or a saved night loaded for one evening: the plan
    says 17 and the bed was asked for 16, so it ran 16."""
    run = trials.build_run(
        plan, readings(plan, asked=lambda at, s: 16 if s.stage is Stage.DEEP else s.temp_c), []
    )
    assert run.part("deep").set_c == 16


def test_a_nudge_leaves_the_setting_but_takes_the_part_out(plan):
    deep = plan.steps[1]
    nudge_from, nudge_to = deep.starts_at + timedelta(hours=1), deep.starts_at + timedelta(hours=1, minutes=30)

    def asked(at, step):
        return step.temp_c + 1 if nudge_from <= at < nudge_to else step.temp_c

    run = trials.build_run(plan, readings(plan, asked=asked), [nudge_from])
    part = run.part("deep")
    assert part.set_c == 17 and part.held == pytest.approx(210 / 240, abs=0.01)
    assert part.by_hand and not part.counts
    assert run.part("rem").counts, "a hand in Deep says nothing about REM"


def test_a_part_mostly_on_something_else_does_not_count(plan):
    deep = plan.steps[1]

    def asked(at, step):
        if step is not deep:
            return step.temp_c
        into = (at - deep.starts_at).total_seconds() / 60
        return 17 if into < 100 else 18 if into < 180 else None

    part = trials.build_run(plan, readings(plan, asked=asked), []).part("deep")
    assert part.set_c == 17 and part.held == pytest.approx(100 / 240, abs=0.01)
    assert not part.counts


def test_nothing_asked_for_is_nothing_run(plan):
    run = trials.build_run(plan, readings(plan, asked=lambda at, s: None), [])
    assert all(p.set_c is None and p.held == 0 and not p.counts for p in run.parts)
    assert all(p.bed_c is None for p in run.parts)


def test_no_readings_at_all_is_nothing_run(plan):
    run = trials.build_run(plan, [], [])
    assert [p.set_c for p in run.parts] == [None] * 4 and run.room_c is None


def test_the_bed_falls_back_to_the_outgoing_hose(plan):
    rows = [r._replace(return_c=None, flow_c=(r.target_c or 0) - 1.0) for r in readings(plan)]
    assert trials.build_run(plan, rows, []).part("deep").bed_c == 16.0


def test_the_room_is_only_from_lights_out_to_the_wake_time(plan):
    rows = readings(plan, room=19.0, before=timedelta(hours=1))
    warm = [r._replace(room_c=25.0) if r.at < plan.bedtime_at else r for r in rows]
    assert trials.build_run(plan, warm, []).room_c == 19.0


# --- Keeping them ---------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "s.db")
    yield database
    database.close()


NOON = datetime.combine(WAKE, time(12, 0))


def test_a_night_comes_back_exactly_as_it_was_kept(db, plan):
    run = trials.build_run(plan, readings(plan), [plan.steps[2].starts_at])
    db.save_night_run(run, NOON)
    assert db.night_runs() == [run]


def test_a_night_worked_out_afterwards_never_replaces_the_mornings_own(db, plan):
    exact = trials.build_run(plan, readings(plan), [])
    db.save_night_run(exact, NOON)
    later = trials.build_run(plan, readings(plan, asked=lambda at, s: 15), [], rebuilt=True)
    db.save_night_run(later, NOON)
    assert db.night_runs() == [exact]


def test_the_mornings_own_replaces_one_worked_out_afterwards(db, plan):
    db.save_night_run(trials.build_run(plan, readings(plan), [], rebuilt=True), NOON)
    exact = trials.build_run(plan, readings(plan), [])
    db.save_night_run(exact, NOON)
    assert db.night_runs() == [exact]


def test_a_test_marked_on_the_night_survives_it_being_written_again(db, plan):
    from dataclasses import replace

    run = trials.build_run(plan, readings(plan), [], rebuilt=True)
    db.save_night_run(replace(run, test_part="deep", test_offset_c=-1), NOON)
    db.save_night_run(trials.build_run(plan, readings(plan), []), NOON)
    kept = db.night_runs()[0]
    assert (kept.test_part, kept.test_offset_c, kept.rebuilt) == ("deep", -1, False)


def test_nights_come_back_oldest_first_between_two_mornings(db):
    for days in (3, 1, 2):
        p = schedule().plan_for(WAKE - timedelta(days=days))
        db.save_night_run(trials.build_run(p, readings(p), []), NOON)
    got = db.night_runs("2026-09-22", "2026-09-23")
    assert [r.wake_on for r in got] == ["2026-09-22", "2026-09-23"]


# --- Written down by the service ------------------------------------------------------


def mat_night(wake_on: date) -> parse.Night:
    """Enough of a night on the mat for record_missing to know the morning had one."""
    start = int(datetime.combine(wake_on - timedelta(days=1), time(23, 40), LONDON).timestamp())
    end = start + 7 * 3600
    return parse.Night(
        id=start, wake_on=wake_on.isoformat(), start_at=start, end_at=end,
        timezone="Europe/London", modified=end, completed=True,
        data={"total_sleep_time": 6 * 3600}, events=None,
    )


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(), echo=False)
    svc.schedule = schedule()
    yield svc
    svc.db.close()


def keep(svc: Service, rows: list[Sample]) -> None:
    for r in rows:
        svc.db.add_power_sample(r.at, r.watts, flow_c=r.flow_c, return_c=r.return_c,
                                room_c=r.room_c, target_c=r.target_c)
    svc.db.flush_power()


def test_the_morning_report_writes_the_night_down(service):
    plan = service.schedule.plan_for(WAKE)
    keep(service, readings(plan))
    service.clock.jump_to(plan.wake_at + timedelta(minutes=20))
    service._send_report(plan)
    [run] = service.db.night_runs()
    assert run.wake_on == WAKE.isoformat() and run.rebuilt is False
    assert run.part("deep").set_c == 17


def test_a_rehearsal_is_nobodys_night(service):
    from dataclasses import replace

    plan = replace(service.schedule.plan_for(WAKE), rehearsal=True)
    assert service.record_night(plan) is None
    assert service.db.night_runs() == []


def test_earlier_mornings_on_the_mat_are_filled_in_but_never_today(service):
    for days in (0, 1, 2, 3):
        morning = WAKE - timedelta(days=days)
        service.db.save_sleep_night(mat_night(morning))
        keep(service, readings(service.schedule.plan_for(morning)))
    service.clock.jump_to(datetime.combine(WAKE, time(9, 0)))

    assert service.record_missing() == 3
    runs = service.db.night_runs()
    assert [r.wake_on for r in runs] == ["2026-09-21", "2026-09-22", "2026-09-23"]
    assert all(r.rebuilt and r.part("deep").set_c == 17 for r in runs)
    assert service.record_missing() == 0, "and never the same morning twice"


def test_filling_in_never_touches_what_the_morning_wrote(service):
    morning = WAKE - timedelta(days=1)
    plan = service.schedule.plan_for(morning)
    service.db.save_sleep_night(mat_night(morning))
    keep(service, readings(plan))
    service.record_night(plan)
    service.clock.jump_to(datetime.combine(WAKE, time(9, 0)))
    assert service.record_missing() == 0
    assert service.db.night_runs()[0].rebuilt is False


def test_the_morning_writes_down_what_the_night_used(service):
    """The same figure the morning report gives, from the same readings, so
    Trends and the push can never disagree about a night's energy."""
    from hydrosnooze import report

    morning = WAKE - timedelta(days=1)
    plan = service.schedule.plan_for(morning)
    keep(service, readings(plan))
    run = service.record_night(plan)
    start, end = report.window(plan)
    assert run is not None and run.kwh is not None
    assert run.kwh == report.kwh(service.db.night_history(start, end))
    assert service.db.night_runs()[0].kwh == run.kwh
