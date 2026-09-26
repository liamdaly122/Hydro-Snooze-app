"""The evening suggestion: tonight's Deep and REM, from the scoreboard.

The chooser first, on hand-made scoreboards, then the service around it: when a
suggestion is offered, what taking one changes, and how the morning knows a
night was a test.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze import suggest
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Schedule, Stage
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import Service
from hydrosnooze.suggest import Limit

USUAL = {"deep": 17, "rem": 20}
LIMITS = {"deep": Limit(17, 2), "rem": Limit(20, 2)}
DATES = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(300)]


def setting(set_c, nights=10, tests=0, awake_m=20, latency_m=15):
    return {"set_c": set_c, "nights": nights, "tests": tests,
            "awake_s": awake_m * 60, "asleep_after_s": latency_m * 60}


def scored(part, settings=(), verdict=None, leader=None, runner=None):
    settings = list(settings)
    return {
        "part": part, "label": suggest.LABEL.get(part, part), "settings": settings,
        "verdict": verdict or ("empty" if not settings else "one_setting"),
        "leader_c": leader, "runner_c": runner,
    }


def board(deep=None, rem=None):
    return {"parts": [deep or scored("deep"), rem or scored("rem"), scored("drift")]}


def choose(b=None, *, on="2026-09-25", usual=USUAL, limits=LIMITS, lowest=15, highest=35):
    return suggest.choose(b or board(), usual, limits, on, lowest=lowest, highest=highest)


# --- The chooser -----------------------------------------------------------------------


def test_the_same_evening_always_gets_the_same_suggestion():
    assert choose(on="2026-09-25") == choose(on="2026-09-25")


def test_about_one_night_in_three_is_a_test_and_the_rest_run_the_usual():
    picks = [choose(on=d) for d in DATES]
    tests = [c for c in picks if c.test_part is not None]
    assert 0.25 < len(tests) / len(picks) < 0.42
    assert all(c.tonight == USUAL for c in picks if c.test_part is None)
    assert all(not c.changes_anything for c in picks if c.test_part is None)


def test_a_test_is_one_degree_in_one_part_and_never_outside_the_limits():
    tight = {"deep": Limit(17, 1), "rem": Limit(20, 1)}
    for d in DATES:
        c = choose(on=d, limits=tight)
        if c.test_part is None:
            continue
        moved = {p for p in suggest.PARTS if c.tonight[p] != c.best[p]}
        assert moved == {c.test_part} and abs(c.test_offset_c) == 1
        assert all(tight[p].holds(c.tonight[p]) for p in suggest.PARTS)


def test_the_part_with_fewer_tests_is_tested():
    b = board(deep=scored("deep", [setting(17, tests=6)]), rem=scored("rem", [setting(20)]))
    tested = {c.test_part for c in (choose(b, on=d) for d in DATES) if c.test_part}
    assert tested == {"rem"}


def test_the_side_with_fewer_nights_is_tested():
    b = board(
        deep=scored("deep", [setting(17, tests=9)]),
        rem=scored("rem", [setting(19, nights=6), setting(20, nights=10)]),
    )
    offsets = {c.test_offset_c for c in (choose(b, on=d) for d in DATES) if c.test_part == "rem"}
    assert offsets == {1}, "21 has no nights yet, 19 has six"


def test_at_the_edge_of_the_limits_only_the_inside_is_tried():
    b = board(deep=scored("deep", [setting(18)]), rem=scored("rem", [setting(20, tests=9)]))
    edge = {"deep": Limit(17, 1), "rem": Limit(20, 2)}
    picks = [choose(b, on=d, usual={"deep": 18, "rem": 20}, limits=edge) for d in DATES]
    assert {c.test_offset_c for c in picks if c.test_part == "deep"} == {-1}


def test_never_past_what_the_unit_can_do():
    picks = [choose(on=d, usual={"deep": 15, "rem": 20},
                    limits={"deep": Limit(15, 2), "rem": Limit(20, 2)}) for d in DATES]
    assert all(c.tonight["deep"] >= 15 for c in picks)


def test_a_clear_leader_inside_the_limits_becomes_the_best():
    b = board(deep=scored("deep", [setting(16), setting(17)], "clear", 16, 17))
    c = choose(b)
    assert c.best["deep"] == 16
    assert "Deep at 16°" in c.why


def test_not_sure_yet_keeps_the_usual():
    b = board(deep=scored("deep", [setting(16), setting(17)], "not_sure", 16, 17))
    assert choose(b).best["deep"] == 17


def test_a_leader_outside_the_limits_is_not_followed():
    b = board(deep=scored("deep", [setting(14), setting(17)], "clear", 14, 17))
    assert choose(b).best["deep"] == 17


def test_a_leader_that_costs_time_awake_is_not_followed():
    b = board(deep=scored("deep", [setting(16, awake_m=35), setting(17, awake_m=20)],
                          "clear", 16, 17))
    assert choose(b).best["deep"] == 17


def test_a_leader_that_costs_time_falling_asleep_is_not_followed():
    b = board(deep=scored("deep", [setting(16, latency_m=30), setting(17, latency_m=15)],
                          "clear", 16, 17))
    assert choose(b).best["deep"] == 17


def test_a_usual_moved_outside_the_limits_is_brought_back_inside_them():
    c = choose(usual={"deep": 21, "rem": 20})
    assert c.best["deep"] == 19


def test_bringing_it_back_inside_says_so_rather_than_calling_it_the_usual():
    """It is a change from the usual, so it cannot be explained as the usual."""
    c = next(c for c in (choose(usual={"deep": 21, "rem": 20}, on=d) for d in DATES)
             if c.test_part is None)
    assert c.changes_anything
    assert "runs your usual" not in c.why
    assert "Deep at 19°" in c.why


# --- Offered, taken and marked ---------------------------------------------------------


EVENING = datetime.combine(date(2026, 9, 24), time(18, 0))


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(EVENING), echo=False)
    svc.schedule = Schedule(days_of_week=list(range(7)))
    monkeypatch.setattr(svc, "_mat_ready", lambda: True)
    yield svc
    svc.db.close()


@pytest.fixture
def always_a_test(monkeypatch):
    monkeypatch.setattr(suggest, "TEST_EVERY", 1)


def usual(svc, part):
    return svc.schedule.stage(Stage(part)).temp_c


def test_nothing_is_offered_before_the_evening(service):
    service.clock.jump_to(datetime.combine(date(2026, 9, 24), time(9, 0)))
    assert service.suggestion()["state"] == "closed"


def test_nothing_is_offered_without_the_mat(service, monkeypatch):
    monkeypatch.setattr(service, "_mat_ready", lambda: False)
    assert service.suggestion()["state"] == "no_mat"


def test_a_test_night_is_offered_in_the_evening(service, always_a_test):
    offered = service.suggestion()
    assert offered["state"] == "ready" and offered["wake_on"] == "2026-09-25"
    assert offered["test"]["part"] in ("deep", "rem")
    moved = [p for p in offered["parts"] if p["tonight_c"] != p["usual_c"]]
    assert len(moved) == 1 and moved[0]["test"]
    assert offered["why"].startswith("A test night")


def test_taking_it_changes_tonight_and_never_the_routine(service, always_a_test):
    offered = service.suggestion()
    after = asyncio.run(service.accept_suggestion())
    assert after["state"] == "accepted"
    running = {s.stage.value: s.temp_c for s in service.tonight_now().stages}
    for p in offered["parts"]:
        assert running[p["part"]] == p["tonight_c"]
        assert usual(service, p["part"]) == p["usual_c"], "the routine is untouched"
    with pytest.raises(CommandFailed):
        asyncio.run(service.accept_suggestion())


def test_turning_it_down_leaves_tonight_alone(service, always_a_test):
    assert service.decline_suggestion()["state"] == "declined"
    assert service.tonight_state() is None
    with pytest.raises(CommandFailed):
        service.decline_suggestion()


def test_taking_it_then_going_back_to_usual_is_undone(service, always_a_test):
    asyncio.run(service.accept_suggestion())
    service.clear_tonight()
    assert service.suggestion()["state"] == "undone"


def test_a_night_already_changed_by_hand_is_left_alone(service):
    asyncio.run(service.set_stage_tonight(Stage.DEEP, usual(service, "deep") + 1))
    assert service.suggestion()["state"] == "by_hand"


def keep_night(svc: Service) -> None:
    """Readings across tonight as it is now being run."""
    wake_on = date(2026, 9, 25)
    plan = svc.tonight_now().plan_for(wake_on)
    at = plan.bedtime_at
    while at < plan.wake_at:
        step = next(s for s in plan.steps if s.starts_at <= at < s.ends_at)
        svc.db.add_power_sample(at, 170.0, return_c=step.temp_c + 0.5, target_c=step.temp_c)
        at += timedelta(minutes=1)
    svc.db.flush_power()


def test_the_morning_marks_a_test_night_as_one(service, always_a_test):
    offered = asyncio.run(service.accept_suggestion())
    keep_night(service)
    run = service.record_night(service.tonight_now().plan_for(date(2026, 9, 25)))
    assert (run.test_part, run.test_offset_c) == (
        offered["test"]["part"], offered["test"]["offset_c"])


def test_a_test_that_did_not_run_is_not_marked(service, always_a_test):
    asyncio.run(service.accept_suggestion())
    service.clear_tonight()
    keep_night(service)
    run = service.record_night(service.schedule.plan_for(date(2026, 9, 25)))
    assert run.test_part is None


def test_the_limits_are_set_once_and_do_not_follow_the_schedule(service):
    first = {x["part"]: (x["low_c"], x["high_c"]) for x in service.suggestion()["limits"]}
    deep = usual(service, "deep")
    assert first["deep"] == (deep - 2, deep + 2)

    service.schedule = replace(service.schedule, stages=[
        replace(s, temp_c=s.temp_c + 2) if s.stage is Stage.DEEP else s
        for s in service.schedule.stages
    ])
    again = {x["part"]: (x["low_c"], x["high_c"]) for x in service.suggestion()["limits"]}
    assert again == first

    widened = service.set_suggestion_reach(3)
    now = {x["part"]: (x["low_c"], x["high_c"]) for x in widened["limits"]}
    assert now["deep"] == (deep + 2 - 3, deep + 2 + 3) and widened["reach"] == 3


def test_the_reach_is_one_to_three(service):
    for reach in (0, 4):
        with pytest.raises(CommandFailed):
            service.set_suggestion_reach(reach)
