"""Step three: tonight's Deep and REM chosen and set without asking.

With Autopilot on, the evening's suggestion is taken by Autopilot itself, so
nobody has to open the app before bed. Back to usual still undoes a night, and
the switch still turns all of it off.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hydrosnooze import suggest
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Schedule, Stage
from hydrosnooze.service import AUTO_EVERY, Service
from hydrosnooze.withings import parse

EVENING = datetime.combine(date(2026, 9, 24), time(18, 0))
MORNING = date(2026, 9, 25)
LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(EVENING), echo=False)
    svc.schedule = Schedule(days_of_week=list(range(7)))
    monkeypatch.setattr(svc, "_mat_ready", lambda: True)
    monkeypatch.setattr(suggest, "TEST_EVERY", 1)
    yield svc
    svc.db.close()


def choose(svc: Service, at: datetime = EVENING) -> None:
    asyncio.run(svc._choose_tonight(at))


def decided(svc: Service):
    return svc.db.decision_for(MORNING.isoformat())


def test_autopilot_chooses_tonight_in_the_evening(service):
    choose(service)
    got = service.suggestion()
    assert got["state"] == "accepted" and got["auto"] is True
    assert service.tonight_suggested() is not None
    said = [e.message for e in service.events.recent() if "Autopilot chose" in e.message]
    assert said and "Back to usual" in said[-1]
    assert service.schedule.stage(Stage.DEEP).temp_c == Schedule(days_of_week=[]).stage(
        Stage.DEEP
    ).temp_c, "the routine is untouched"


def test_nothing_is_chosen_with_autopilot_off(service):
    asyncio.run(service.set_autopilot(False))
    choose(service)
    assert decided(service) is None and service.tonight_state() is None


def test_nothing_is_chosen_without_the_mat(service, monkeypatch):
    monkeypatch.setattr(service, "_mat_ready", lambda: False)
    choose(service)
    assert decided(service) is None


def test_a_night_set_by_hand_is_left_alone(service):
    asyncio.run(service.set_stage_tonight(Stage.DEEP, 15))
    choose(service)
    assert decided(service) is None
    assert service.tonight_now().stage(Stage.DEEP).temp_c == 15


def test_back_to_usual_stays_back_to_usual(service):
    choose(service)
    service.clear_tonight()
    choose(service, EVENING + AUTO_EVERY)
    assert service.suggestion()["state"] == "undone"
    assert service.tonight_state() is None


def test_it_looks_every_ten_minutes_not_every_beat(service, monkeypatch):
    asked = []
    real = service.suggestion
    monkeypatch.setattr(service, "suggestion", lambda: asked.append(1) or real())
    monkeypatch.setattr(suggest, "TEST_EVERY", 10**9)  # a usual night: nothing to take
    for minutes in (0, 1, 5, 9):
        choose(service, EVENING + timedelta(minutes=minutes))
    assert len(asked) == 1
    choose(service, EVENING + AUTO_EVERY)
    assert len(asked) == 2


def test_a_usual_night_changes_nothing(service, monkeypatch):
    monkeypatch.setattr(suggest, "TEST_EVERY", 10**9)
    choose(service)
    assert decided(service) is None and service.tonight_state() is None
    assert service.suggestion()["state"] == "usual"


def test_switching_off_forgets_the_choice_and_on_again_chooses_again(service):
    choose(service)
    asyncio.run(service.set_autopilot(False))
    assert decided(service) is None and service.tonight_suggested() is None
    asyncio.run(service.set_autopilot(True))
    choose(service, EVENING + timedelta(minutes=1))
    assert decided(service).auto is True and service.tonight_suggested() is not None


def test_a_suggestion_somebody_tapped_stays_answered_through_the_switch(service):
    asyncio.run(service.accept_suggestion())
    asyncio.run(service.set_autopilot(False))
    kept = decided(service)
    assert kept is not None and kept.auto is False


def test_the_morning_report_says_how_the_test_went(service):
    choose(service)
    test = service.suggestion()["test"]
    plan = service.tonight_now().plan_for(MORNING)
    at = plan.bedtime_at
    while at < plan.wake_at:
        step = next(s for s in plan.steps if s.starts_at <= at < s.ends_at)
        service.db.add_power_sample(at, 170.0, return_c=step.temp_c + 0.5, target_c=step.temp_c)
        at += timedelta(minutes=1)
    service.db.flush_power()
    start = int(datetime.combine(MORNING - timedelta(days=1), time(23, 0), LONDON).timestamp())
    service.db.save_sleep_night(parse.Night(
        id=start, wake_on=MORNING.isoformat(), start_at=start, end_at=start + 7 * 3600,
        timezone="Europe/London", modified=start + 7 * 3600, completed=True,
        data={"deepsleepduration": 5400, "remsleepduration": 6600, "total_sleep_time": 25000},
        events=None,
    ))

    service.clock.jump_to(plan.wake_at + timedelta(minutes=20))
    service._send_report(plan)
    body = [e.message for e in service.events.recent() if e.kind == "report"][-1]
    label = suggest.LABEL[test["part"]]
    assert f"Autopilot tested {label} at " in body
    assert "a degree cooler than usual." in body or "a degree warmer than usual." in body
