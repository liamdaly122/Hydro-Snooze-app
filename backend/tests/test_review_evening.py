"""Four more from the review of 22 September, as failing tests first.

Each one is the bed being told something other than what was decided: a typed
number moved by a nudge it was meant to replace, a pre-heat still being timed
after the unit was switched off, stages driven off a clock nothing had
confirmed, and a night that forgot it had started once the bed got warm.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from hydrosnooze import clocksync
from hydrosnooze.adapters.probes import FLOW, RETURN, ROOM, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import DRIFT_MINUTES, Mode, Power, Schedule, SleepStage, Stage
from hydrosnooze.scheduler import Job
from hydrosnooze.service import PreconditionRun, Service

NOW = datetime(2026, 9, 12, 20, 0)
TONIGHT = date(2026, 9, 13)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def forget_the_clock():
    clocksync.reset()
    yield
    clocksync.reset()


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False)
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 21),
            SleepStage(Stage.DEEP, 240, 19),
            SleepStage(Stage.REM, 210, 22),
            SleepStage(Stage.WAKE, 60, 26),
        ],
    )
    svc.load_tonight()
    yield svc
    svc.db.close()


def hoses(service, flow: float, back: float, room: float = 19.6) -> None:
    at = service.clock.now()
    for name, value in ((FLOW, flow), (RETURN, back), (ROOM, room)):
        service.probes.readings[name] = Reading(value, at)


async def in_deep(service):
    plan = service.schedule.plan_for(TONIGHT)
    deep = next(s for s in plan.steps if s.stage is Stage.DEEP)
    service.clock.jump_to(deep.starts_at + timedelta(hours=1))
    await service._run_stage(plan, deep)
    # One beat of the sampler, which is what says which stage is running.
    await service._sample_power()
    assert service.state.current_stage is Stage.DEEP
    return plan, deep


# --- A number typed by hand is the number ----------------------------------------


async def test_a_typed_temperature_is_not_moved_by_a_nudge(service):
    """A degree cooler for half an hour, then 22 typed on the Now tab. It sent
    22, and then straight away a second full sequence to 21, because the stage
    was changed for tonight and following the plan applied the nudge to it. The
    comment on set_temperature said typed numbers are never nudged."""
    await in_deep(service)
    await service.nudge_tonight(-1)
    assert service.state.assumed_target_c == 18

    await service.set_temperature(22)
    assert service.state.assumed_target_c == 22

    # And the sampling beat does not put the nudge back half a minute later.
    service._followed_at = None
    await service._follow_the_plan()
    assert service.state.assumed_target_c == 22


async def test_typing_a_temperature_ends_the_nudge_and_says_so(service):
    await in_deep(service)
    await service.nudge_tonight(-1)

    await service.set_temperature(22)

    tonight = service.tonight_state()
    assert tonight is not None and tonight.nudge_at(service.clock.now()) == 0
    assert any("nudge" in e.message.lower() for e in service.events.recent(5))


# --- A pre-heat that is over is not still being timed ----------------------------


def timing(service, mode=Mode.WARMING, target=28) -> None:
    """A pre-heat in flight, with the hoses having seen the gap open."""
    service._precondition = PreconditionRun(
        started=service.clock.now(), mode=mode, target_c=target, start_c=20.0
    )
    service.clock.advance(timedelta(minutes=5))
    hoses(service, 34.5, 31.0)
    service._watch_precondition(300.0, service.clock.now())
    assert service._precondition is not None and service._precondition.worked


def settled_off(service) -> None:
    """Ten minutes after the unit stopped: both hoses at the room."""
    service.clock.advance(timedelta(minutes=10))
    hoses(service, 21.0, 21.0)
    service._watch_precondition(None, service.clock.now())


async def switch_off(service):
    await service.power_off()


async def press(service):
    await service.press_power()


async def bedside(service):
    await service._button_power_toggle("Bedside: on/off")


async def stop_test(service):
    service.scheduler.rehearsal = service.schedule.plan_for(TONIGHT)
    await service.stop_rehearsal(power_off=False)


async def scheduled_off(service):
    await service._run_power_off(service.schedule.plan_for(TONIGHT))


@pytest.mark.parametrize(
    "how", [switch_off, press, bedside, stop_test, scheduled_off], ids=lambda f: f.__name__
)
async def test_switching_off_mid_pre_heat_records_nothing(service, how):
    """The hoses meet at room temperature once the unit stops, and a run still
    in flight read that as the bed having arrived: reached, at 21C, decided by
    the probes. Three of those and every warming command goes out 4C high."""
    timing(service)

    await how(service)
    settled_off(service)

    assert service._precondition is None
    assert service.db.precondition_runs() == []


async def test_the_plug_seeing_the_unit_off_ends_the_run_too(service):
    """Switched off with the remote, which nothing here hears about. The plug
    does, and a run whose unit is off has nothing left to measure."""
    service.unit.powered = True
    timing(service)
    service.unit.powered = False
    service.clock.advance(timedelta(minutes=5))
    await service._sample_power()
    assert service.state.power is Power.OFF, "the plug has caught up"

    assert service._precondition is None
    settled_off(service)
    assert service.db.precondition_runs() == []


# --- Not driving a night off a clock nobody has confirmed ------------------------


async def test_the_sampling_beat_waits_for_the_clock_like_the_tick_does(service, monkeypatch):
    """A Pi rebooted mid-night comes back believing it is whenever it last
    wrote the time down. The tick waits for the network before doing anything;
    the sampling beat did not, found an earlier stage with stage_now, and sent
    its temperature. Once the clock caught up the half hour dwell held the bed
    there."""
    await in_deep(service)
    # Deep at 19, and the unit believes it is somewhere else entirely.
    service._set_state(assumed_target_c=24, power=Power.ON)
    service._followed_at = None
    service._clock_ok = False
    monkeypatch.setattr(clocksync, "synchronised", lambda: False)

    before = len(service.transmitter.sent)
    kept = len(service.db.power_history(service.clock.now() - timedelta(hours=1)))
    await service._sample_power()

    assert len(service.transmitter.sent) == before, "nothing pressed on an unconfirmed clock"
    assert service.state.assumed_target_c == 24
    assert service.state.current_stage is None
    assert service.state.observed_power_w, "still watched"
    assert len(service.db.power_history(service.clock.now() - timedelta(hours=1))) == kept, (
        "and not filed under a time nothing has confirmed"
    )


# --- A night that has started getting ready stays started -------------------------


def warm_first(service) -> None:
    service.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DRIFT, DRIFT_MINUTES, 30),
            SleepStage(Stage.DEEP, 240, 26),
            SleepStage(Stage.REM, 210, 24),
            SleepStage(Stage.WAKE, 60, 28),
        ],
    )


def bed(service, c: float) -> None:
    service.probes.readings[RETURN] = Reading(c, service.clock.now())


async def pre_heating(service):
    """Start tonight's pre-heat from a 20C bed, the way the tick would."""
    warm_first(service)
    service.clock.jump_to(datetime(2026, 9, 12, 21, 0))
    bed(service, 20.0)
    plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert plan is not None and plan.precool_at is not None
    assert plan.preconditioning.mode is Mode.WARMING
    service.clock.jump_to(plan.precool_at)
    bed(service, 20.0)
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "precool", job
    assert await service._run_precool(job.plan)
    service.scheduler.fired.mark(job)
    return plan


async def test_the_night_stays_running_once_the_bed_is_nearly_there(service):
    """Pre-heat to 30C from a 20C bed. A minute before bedtime the bed is at
    29.5, the plan was rebuilt from that, found nothing left to do, dropped
    precool_at, and the night was 'evening' again: Bed early came back and the
    nudge went away, in the middle of getting ready."""
    plan = await pre_heating(service)

    service.clock.jump_to(plan.bedtime_at - timedelta(minutes=1))
    bed(service, 29.5)

    now_plan = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert now_plan is not None
    assert now_plan.precool_at == plan.precool_at
    assert now_plan.preconditioning.mode is Mode.WARMING
    assert service.tonight_phase() == "running"


async def test_skipping_mid_pre_heat_still_switches_the_unit_off(service):
    """The dangerous half. Skip before a night starts drops the night outright,
    switch-off included, and 'starts' was worked out from a precool_at that had
    just vanished. Skipping a pre-heat that was already running left the unit
    heating the bed with nothing booked to turn it off."""
    plan = await pre_heating(service)
    service.clock.jump_to(plan.bedtime_at - timedelta(minutes=1))
    bed(service, 29.5)

    service.skip_tonight(True)

    kept = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert kept is not None and kept.wake_at == plan.wake_at, "tonight, with its switch-off"
    service.clock.jump_to(plan.wake_at)
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "power_off"


async def test_what_was_decided_survives_a_restart(service, tmp_path):
    plan = await pre_heating(service)

    again = Service(service.settings, clock=service.clock, echo=False)
    again.schedule = service.schedule
    again.load_tonight()
    again.scheduler.fired.load(again.db.fired_marks())
    again.clock.jump_to(plan.bedtime_at - timedelta(minutes=1))
    again.probes.readings[RETURN] = Reading(29.5, again.clock.now())

    now_plan = again.scheduler.plan_in_progress(again.schedule, again.clock.now())
    assert now_plan is not None and now_plan.precool_at == plan.precool_at
    again.db.close()


async def test_the_morning_report_covers_the_whole_pre_heat(service):
    plan = await pre_heating(service)
    service.clock.jump_to(plan.wake_at + timedelta(hours=1))
    finished = service.scheduler.last_finished(service.schedule, service.clock.now())
    assert finished is not None and finished.precool_at == plan.precool_at


async def test_a_rehearsal_does_not_hold_the_real_night(service):
    """A rehearsal pre-conditions too, and it is not tonight."""
    warm_first(service)
    bed(service, 20.0)
    rehearsal = await service.start_rehearsal(600)
    if rehearsal.precool_at is not None:
        await service._run_precool(rehearsal)
    await service.stop_rehearsal(power_off=False)

    service.clock.jump_to(datetime(2026, 9, 12, 21, 0))
    bed(service, 29.5)
    real = service.scheduler.plan_in_progress(service.schedule, service.clock.now())
    assert real is not None and real.preconditioning.mode is None, "worked out afresh"


async def test_the_job_key_is_the_same_either_side_of_the_hold():
    """What stops the pre-heat being offered twice: the mark does not care
    when it was planned for."""
    schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[SleepStage(Stage.DEEP, 240, 30)],
    )
    a = schedule.plan_for(TONIGHT, bed_c=20.0)
    b = schedule.plan_for(TONIGHT, bed_c=26.0)
    assert Job("precool", a).key == Job("precool", b).key


async def test_the_card_says_what_is_being_done_while_it_is_done(service):
    plan = await pre_heating(service)
    service.clock.jump_to(plan.bedtime_at - timedelta(minutes=1))
    bed(service, 29.5)

    shown = service.schedule_as_shown()["preconditioning"]
    assert shown["mode"] == "warming", shown
