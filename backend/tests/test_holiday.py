"""Holiday mode: nothing switches on while you are away, and the routine is left alone.

Two dates, the way anyone says them: the day you leave and the day you get back.
The first night off is the evening you leave; the bed runs again the evening you
get home. Every night here is named by its wake morning, which is where most of
the ways to get this wrong live, so the tests below pin the edges down by date.

The other half is what it must not do. It is not a change to the routine, so the
days of the week and the Run automatically switch are exactly as they were, and
it ends on its own on the day you said.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.main import app as real_app
from hydrosnooze.models import (
    DRIFT_MINUTES,
    Holiday,
    Power,
    Schedule,
    SleepStage,
    Stage,
)
from hydrosnooze.service import Service

# A Thursday evening. Leaving on Friday, back on Monday: Friday, Saturday and
# Sunday nights are off, which are the Saturday, Sunday and Monday mornings.
NOW = datetime(2026, 10, 1, 20, 0)
LEAVE = date(2026, 10, 2)
BACK = date(2026, 10, 5)


def _schedule() -> Schedule:
    return Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 19),
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    )


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = _schedule()
    svc.load_tonight()
    yield svc
    svc.db.close()


def plan_at(service, when: datetime):
    return service.scheduler.plan_in_progress(service.schedule, when)


def due_at(service, when: datetime):
    return service.scheduler.due(service.schedule, when)


# --- Which nights ---------------------------------------------------------------


def test_the_nights_off_are_from_the_evening_you_leave_to_the_evening_you_get_back():
    holiday = Holiday(LEAVE, BACK)

    assert not holiday.away_on(date(2026, 10, 2)), "Thursday night, before you go, runs"
    assert holiday.away_on(date(2026, 10, 3)), "Friday night, the night you leave, is off"
    assert holiday.away_on(date(2026, 10, 4))
    assert holiday.away_on(date(2026, 10, 5)), "Sunday night, the last one away, is off"
    assert not holiday.away_on(date(2026, 10, 6)), "Monday night, the night you get back, runs"
    assert holiday.nights == 3


async def test_the_night_before_you_leave_still_runs_and_switches_off(service):
    """Setting it on the Thursday evening is the usual way it will be used, and
    Thursday night is somebody's last night in their own bed. It has to run, and
    above all it has to switch off on Friday morning."""
    await service.set_holiday(LEAVE, BACK)

    plan = plan_at(service, NOW)
    assert plan is not None and plan.wake_at == datetime(2026, 10, 2, 7, 30)

    job = due_at(service, datetime(2026, 10, 2, 7, 30))
    assert job is not None and job.kind == "power_off"


async def test_nothing_runs_while_you_are_away(service):
    """Every quarter of an hour from Friday morning to Monday morning. Not a
    pre-heat, not a blaster restart, not a stage, and not a single missed stage
    reported at 3am for a night nobody was there for."""
    await service.set_holiday(LEAVE, BACK)
    scheduler, schedule = service.scheduler, service.schedule

    at = datetime(2026, 10, 2, 10, 0)
    while at < datetime(2026, 10, 5, 10, 0):
        assert scheduler.due(schedule, at) is None, f"something ran at {at:%a %H:%M}"
        assert scheduler.missed(schedule, at) == [], f"a stage was missed at {at:%a %H:%M}"
        assert scheduler.cancelled(schedule, at) == [], at
        assert scheduler.stage_now(schedule, at) is None, at
        at += timedelta(minutes=15)


async def test_the_first_night_back_runs_as_usual(service):
    await service.set_holiday(LEAVE, BACK)

    plan = plan_at(service, datetime(2026, 10, 5, 20, 0))
    assert plan is not None and plan.wake_at == datetime(2026, 10, 6, 7, 30)

    job = due_at(service, datetime(2026, 10, 5, 22, 35))
    assert job is not None and job.kind == "stage"
    assert job.step is not None and job.step.stage is Stage.DRIFT


async def test_the_routine_is_left_exactly_as_it_was(service):
    """Not a change to the routine. Being away for a week is not deciding you no
    longer sleep on Tuesdays, and getting home should not mean putting days
    back."""
    before = (service.schedule.days_of_week, service.schedule.enabled)
    saved = service.db.load_schedule()
    await service.set_holiday(LEAVE, BACK)
    assert (service.schedule.days_of_week, service.schedule.enabled) == before
    assert service.db.load_schedule() == saved


async def test_the_morning_report_describes_the_last_night_you_were_home(service):
    """A night away has nothing to report. Over the weekend the last finished
    night is the Thursday, and after the first night back it is that one."""
    await service.set_holiday(LEAVE, BACK)

    over = service.scheduler.last_finished(service.schedule, datetime(2026, 10, 4, 12, 0))
    assert over is not None and over.wake_at.date() == date(2026, 10, 2)

    over = service.scheduler.last_finished(service.schedule, datetime(2026, 10, 6, 12, 0))
    assert over is not None and over.wake_at.date() == date(2026, 10, 6)


async def test_a_night_away_says_nothing_at_all(service):
    """The whole service, tick by tick, through a night nobody is home for.

    Past its start time a skipped night looks, to the clock, exactly like one
    skipped at 2am, and that one is wound up: a line for every stage it calls
    off, a switch-off at the alarm, and a morning report. For a week away that is
    seven pushes about a bed nobody was in, which is how you learn to ignore the
    one that matters.
    """
    await service.set_holiday(LEAVE, BACK)
    pushed: list[str] = []
    service.notifier.push = lambda title, body, **_: pushed.append(title)
    marked = len(service.events.recent(500))

    at = datetime(2026, 10, 2, 20, 0)
    while at < datetime(2026, 10, 3, 12, 0):
        service.clock.jump_to(at)
        await service._tick()
        at += timedelta(minutes=5)

    said = service.events.recent(500)[: len(service.events.recent(500)) - marked]
    assert said == [], [e.message for e in said]
    assert pushed == []


async def test_tonights_controls_are_not_offered_while_away(service):
    await service.set_holiday(LEAVE, BACK)
    service.clock.jump_to(datetime(2026, 10, 3, 20, 0))
    assert service.tonight_phase() == "none"


# --- Putting it on and taking it off ---------------------------------------------


async def test_it_survives_a_restart(service, tmp_path):
    """The Pi restarts routinely, and forgetting a holiday would get an empty bed
    ready every night until you got home."""
    await service.set_holiday(LEAVE, BACK)
    service.db.close()

    again = Service(
        Settings(db_path=str(tmp_path / "s.db")),
        clock=VirtualClock(datetime(2026, 10, 3, 20, 0)),
        echo=False,
    )
    try:
        again.schedule = _schedule()
        assert again.holiday_state() == Holiday(LEAVE, BACK)
        assert due_at(again, datetime(2026, 10, 3, 22, 35)) is None
    finally:
        again.db.close()


async def test_it_ends_by_itself_after_the_day_you_get_back(service):
    await service.set_holiday(LEAVE, BACK)

    service.clock.jump_to(datetime(2026, 10, 5, 20, 0))
    assert service.holiday_state() is not None, "still showing on the day you get back"

    service.clock.jump_to(datetime(2026, 10, 6, 8, 0))
    assert service.holiday_state() is None, "and gone the morning after"


async def test_turning_it_off_puts_the_nights_back(service):
    await service.set_holiday(LEAVE, BACK)
    service.clear_holiday()

    plan = plan_at(service, datetime(2026, 10, 2, 20, 0))
    assert plan is not None and plan.wake_at == datetime(2026, 10, 3, 7, 30)
    assert service.holiday_state() is None
    assert service.db.holiday() is None


async def test_changing_the_dates_replaces_them(service):
    await service.set_holiday(LEAVE, BACK)
    await service.set_holiday(date(2026, 10, 9), date(2026, 10, 11))

    assert service.holiday_state() == Holiday(date(2026, 10, 9), date(2026, 10, 11))
    assert plan_at(service, datetime(2026, 10, 2, 20, 0)).wake_at.date() == date(2026, 10, 3)


async def test_getting_back_before_leaving_is_refused(service):
    with pytest.raises(ValueError, match="after the day you leave"):
        await service.set_holiday(BACK, LEAVE)
    with pytest.raises(ValueError, match="after the day you leave"):
        await service.set_holiday(LEAVE, LEAVE)
    assert service.holiday_state() is None


async def test_a_holiday_already_over_is_refused(service):
    with pytest.raises(ValueError, match="already over"):
        await service.set_holiday(date(2026, 9, 20), date(2026, 9, 27))


async def test_it_can_start_tonight(service):
    """Remembering on the evening you leave is the case this has to handle. It
    is eight o'clock and tonight's bed has not started getting ready yet."""
    await service.set_holiday(NOW.date(), BACK)

    assert service.scheduler.away(date(2026, 10, 2))
    plan = plan_at(service, NOW)
    assert plan is not None and plan.wake_at.date() == date(2026, 10, 6)


async def test_it_says_so_in_the_log(service):
    await service.set_holiday(LEAVE, BACK)
    service.clear_holiday()

    said = [e.message for e in service.events.recent(10) if e.kind == "holiday"]
    assert any("Fri 2 Oct to Mon 5 Oct" in m and "3 nights" in m for m in said)
    assert any("Holiday mode is off" in m for m in said)


# --- A night already under way ---------------------------------------------------


async def _a_night_running(service) -> None:
    """Friday night, half past eleven, with the unit on and Deep under way.

    Run through the tick rather than by calling _run_stage, so the stages are
    marked the way a real night marks them. Whether a night away had begun is
    read off those marks.
    """
    for at in (datetime(2026, 10, 2, 22, 31), datetime(2026, 10, 2, 23, 6)):
        service.clock.jump_to(at)
        await service._tick()
    service.clock.jump_to(datetime(2026, 10, 2, 23, 30))
    await service._sample_power()
    assert service.state.power is Power.ON
    assert service.scheduler.fired.keys_for(
        service.schedule.plan_for(date(2026, 10, 3))
    ) == {"stage:drift", "stage:deep"}


async def test_a_night_already_under_way_is_switched_off_now(service):
    """Remembering on the train, after the bed has started. Skipping keeps the
    unit running until morning in case somebody is on it; a holiday is the one
    case where nobody is, and keeping that promise heats an empty bed all night."""
    await _a_night_running(service)

    await service.set_holiday(date(2026, 10, 2), BACK)
    assert service._away_task is not None
    await service._away_task

    assert service.state.power is Power.OFF
    assert service.unit is not None and not service.unit.powered


async def test_the_stages_it_cuts_short_are_called_off_rather_than_missed(service):
    """A stage that did not run because of holiday mode is somebody's decision,
    not a failure, and must never ring the phone."""
    await _a_night_running(service)
    await service.set_holiday(date(2026, 10, 2), BACK)
    await service._away_task

    service.clock.jump_to(datetime(2026, 10, 3, 4, 0))
    await service._tick()

    recent = service.events.recent(40)
    assert not [e for e in recent if e.level == "error"], [e.message for e in recent]
    called_off = [e.message for e in recent if "was not run" in e.message]
    assert called_off and all("holiday mode is on" in m for m in called_off)


async def test_the_usual_switch_off_is_still_owed(service):
    """If switching off early did not land, the morning one is the backstop, and
    if the holiday is cancelled before morning and the night starts again, the
    unit still has to be switched off at the alarm. So it stays on the list."""
    await _a_night_running(service)
    await service.set_holiday(date(2026, 10, 2), BACK)
    await service._away_task

    job = due_at(service, datetime(2026, 10, 3, 7, 30))
    assert job is not None and job.kind == "power_off"


async def test_a_night_under_way_before_the_holiday_starts_is_left_running(service):
    """Setting it on Friday night for a holiday that starts on Saturday: tonight
    is somebody's last night at home, and they are in it."""
    await _a_night_running(service)

    await service.set_holiday(date(2026, 10, 3), date(2026, 10, 10))

    assert service._away_task is None
    assert service.state.power is Power.ON


# --- Over HTTP ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    """The real routes with a service this test controls. See test_tonight_routes
    for why this does not run the app's lifespan."""
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = _schedule()
    real_app.state.service = svc
    real_app.state.build = "test"
    yield TestClient(real_app)
    svc.db.close()


def test_there_is_no_holiday_to_begin_with(client):
    r = client.get("/api/holiday")
    assert r.status_code == 200
    assert r.json() is None


def test_setting_the_dates_answers_with_the_holiday(client):
    r = client.put("/api/holiday", json={"leaves_on": "2026-10-02", "back_on": "2026-10-05"})

    assert r.status_code == 200, r.text
    assert r.json() == {"leaves_on": "2026-10-02", "back_on": "2026-10-05", "nights": 3}
    assert client.get("/api/holiday").json()["back_on"] == "2026-10-05"


def test_dates_the_wrong_way_round_are_refused_in_words(client):
    r = client.put("/api/holiday", json={"leaves_on": "2026-10-05", "back_on": "2026-10-02"})
    assert r.status_code == 422
    assert "after the day you leave" in r.json()["detail"]


def test_something_that_is_not_a_date_is_refused(client):
    r = client.put("/api/holiday", json={"leaves_on": "Friday", "back_on": "2026-10-05"})
    assert r.status_code == 422


def test_turning_it_off_over_http(client):
    client.put("/api/holiday", json={"leaves_on": "2026-10-02", "back_on": "2026-10-05"})

    r = client.delete("/api/holiday")

    assert r.status_code == 200
    assert client.get("/api/holiday").json() is None


def test_the_schedule_on_the_screen_is_not_touched(client):
    before = client.get("/api/schedule").json()
    client.put("/api/holiday", json={"leaves_on": "2026-10-02", "back_on": "2026-10-05"})
    after = client.get("/api/schedule").json()
    assert after["days_of_week"] == before["days_of_week"]
    assert after["enabled"] is True
