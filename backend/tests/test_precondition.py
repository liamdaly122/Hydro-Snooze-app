"""Getting the bed ready before the night starts, and the whole night after it.

A cooler cannot warm a bed. If the first stage is above whatever the bed is
resting at, only warming mode gets there, and only down to 25C.

The arming ordering that used to matter here is gone: the unit's own scheduler is
never armed, so cooling and warming can be switched at any point. What replaces it
is the night itself, driven stage by stage.
"""

from __future__ import annotations

import asyncio

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.adapters.probes import RETURN, Reading
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Mode, Power, Schedule, SleepStage, Stage
from hydrosnooze.scheduler import Job
from hydrosnooze.service import Service


@pytest.fixture
def service(tmp_path):
    settings = Settings(db_path=str(tmp_path / "test.db"), sim_speed=1.0)
    clock = VirtualClock(datetime(2026, 9, 7, 21, 0))
    svc = Service(settings, clock=clock, echo=False)
    yield svc
    svc.db.close()


def _schedule(**kwargs) -> Schedule:
    base = dict(
        wake_time=time(6, 30),
        days_of_week=[0, 1, 2, 3, 4],
        stages=[
            SleepStage(Stage.DEEP, 240, 17),
            SleepStage(Stage.REM, 210, 20),
            SleepStage(Stage.WAKE, 30, 26),
        ],
    )
    base.update(kwargs)
    return Schedule(**base)  # type: ignore[arg-type]


def hoses(service, bed_c: float) -> None:
    """Put a current reading on the return probe, which is the one the bed
    temperature is taken from."""
    service.probes.readings[RETURN] = Reading(bed_c, service.clock.now())


def _warm_first(**kwargs) -> Schedule:
    return _schedule(
        stages=[SleepStage(Stage.DEEP, 240, 26), SleepStage(Stage.WAKE, 30, 28)],
        **kwargs,
    )


# --- Deciding how the bed gets ready ------------------------------------------
#
# Never a setting. The bed starts wherever the hose probes say it is, the first
# stage says where it has to be, and the gap between them decides the mode and
# the head start. With no probes reporting it assumes a room-temperature bed,
# which is what it did for months before there was anything to measure.


@pytest.mark.parametrize("temp", [15, 17, 18])
def test_a_cold_first_stage_pre_cools_in_turbo(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning()
    assert pre.mode is Mode.TURBO
    assert "Cooling the bed" in pre.reason


@pytest.mark.parametrize("temp", [25, 28, 30])
def test_a_warm_first_stage_pre_heats(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning()
    assert pre.mode is Mode.WARMING
    assert "Warming the bed" in pre.reason


@pytest.mark.parametrize("temp", [22, 23, 24])
def test_warmer_than_the_room_but_below_warming_floor_does_nothing(temp):
    """The one gap nothing can close. The bed has to warm, warming mode cannot
    express a number that low, and running the cooler at it would be worse."""
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning()
    assert pre.mode is None
    assert not pre.runs
    assert "25C" in pre.reason


@pytest.mark.parametrize("temp", [19, 20, 21])
def test_a_first_stage_at_room_temperature_does_nothing(temp):
    pre = _schedule(stages=[SleepStage(Stage.DEEP, 240, temp)]).preconditioning()
    assert pre.mode is None
    assert "near enough" in pre.reason


def test_the_head_start_grows_with_the_distance():
    """Not a fixed thirty minutes any more. Three degrees is a shorter job than
    ten, and starting an hour early for three degrees just wastes power."""
    near = _schedule(stages=[SleepStage(Stage.DEEP, 240, 18)]).preconditioning()
    far = _schedule(stages=[SleepStage(Stage.DEEP, 240, 15)]).preconditioning()
    assert far.lead_minutes > near.lead_minutes
    assert 10 <= near.lead_minutes <= 90
    assert 10 <= far.lead_minutes <= 90


def test_nothing_to_do_means_no_pre_conditioning_in_the_plan():
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 20)])
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is None
    assert plan.starts_at == plan.bedtime_at


def test_the_plan_starts_exactly_its_head_start_before_bedtime():
    schedule = _schedule()
    plan = schedule.plan_for(datetime(2026, 9, 8).date())
    lead = timedelta(minutes=schedule.preconditioning().lead_minutes)
    assert plan.precool_at == plan.bedtime_at - lead


# --- A whole night ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_night_cools_then_heats(service):
    """The thing the unit's own scheduler made impossible.

    While its Smart Sleep Schedule runs it refuses to switch between cooling and
    warming, which capped every night at "somewhere at or below the bedroom".
    """
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.mode is Mode.TURBO
    assert service.unit.target == 17

    seen = []
    for step in plan.steps:
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)
        seen.append((step.label, service.unit.mode, service.unit.target))

    assert seen == [
        ("Deep", Mode.QUIET, 17),
        ("REM", Mode.QUIET, 20),
        ("Wake", Mode.WARMING, 26),
    ]


@pytest.mark.asyncio
async def test_the_night_ends_with_the_unit_switched_off(service):
    """Not optional. Nothing else turns it off now."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.powered

    service.clock.jump_to(plan.wake_at)
    await service._run_power_off(plan)
    assert not service.unit.powered


@pytest.mark.asyncio
async def test_a_stage_powers_the_unit_on_if_it_is_off(service):
    # Whether it was never started, or switched itself off, the night should not
    # silently continue against a dead unit.
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    step = plan.steps[1]
    service.clock.jump_to(step.starts_at)
    assert not service.unit.powered

    await service._run_stage(plan, step)
    assert service.unit.powered
    assert service.unit.target == step.temp_c


@pytest.mark.asyncio
async def test_a_night_never_touches_the_mute_button(service):
    """The unit remembers whether it is muted, so this is never automatic.

    Sending it at each power on would unmute it every other night, and the press
    that unmuted it would beep.
    """
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())

    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    for step in plan.steps:
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)

    assert not any("mute" in line for line in service.transmitter.lines)
    assert not service.unit.muted, "left exactly as it was found"


@pytest.mark.asyncio
async def test_pre_heating_leaves_the_bed_warm_before_a_warm_first_stage(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    service.clock.jump_to(plan.precool_at)
    await service._run_precool(plan)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 26


@pytest.mark.asyncio
async def test_a_gap_nothing_can_close_sends_no_presses_and_says_why(service):
    """First stage 23C, room 20C. The bed has to warm and nothing can warm it to
    23C, so the unit stays off rather than running a cooler at a bed needing heat."""
    service.schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 23)])
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is None

    before = len(list(service.transmitter.lines))
    await service._run_precool(plan)

    assert len(list(service.transmitter.lines)) == before, "no presses at all"
    assert any("25C" in e.message for e in service.events.recent(50))


# --- Saying so when it achieved nothing ---------------------------------------


@pytest.mark.asyncio
async def test_a_pre_conditioning_run_that_never_drew_power_is_reported(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 3.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert any("never drew more than" in m for m in messages)


@pytest.mark.asyncio
async def test_a_working_pre_conditioning_run_is_not_reported(service):
    service.schedule = _warm_first()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert plan.precool_at is not None
    for minute in range(0, 30, 5):
        service.db.add_power_sample(plan.precool_at + timedelta(minutes=minute), 290.0)

    service._report_idle_preconditioning(plan)
    messages = [e.message for e in service.events.recent(50) if e.level == "warning"]
    assert not any("never drew more than" in m for m in messages)


@pytest.mark.asyncio
async def test_muting_gives_up_the_target_rather_than_lying_about_it(service):
    """Mute is the one command that cannot put the temperature back.

    Its wake preamble is two temp_down presses, and whichever of them are not
    swallowed really do lower the target. Every other command rails to a mode's
    floor and counts up afterwards, which absorbs them. This one has nothing to
    count to, so the unit ends up a degree or two below what the app last set,
    and the app has to say it no longer knows rather than keep showing the number.
    """
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()
    await service.set_temperature(20)
    assert service.state.assumed_target_c == 20

    await service.mute()

    assert service.unit.muted
    assert service.unit.target < 20, "the wake presses land on a woken display"
    assert service.state.assumed_target_c is None


# --- The overlap, driven through the unit --------------------------------------
#
# Cooling reaches 15 to 35 and warming reaches 25 to 55, so between 25 and 35 both
# modes can hold the number. Only one of them moves the bed there.


@pytest.mark.asyncio
async def test_a_stage_that_drops_into_the_overlap_switches_the_unit_to_cooling(service):
    """Deep at 30C then REM at 25C. Warming would set 25 and then do nothing at
    all while the bed coasted down on its own for the next three hours."""
    service.schedule = _schedule(
        stages=[
            SleepStage(Stage.DEEP, 240, 30),
            SleepStage(Stage.REM, 210, 25),
            SleepStage(Stage.WAKE, 30, 26),
        ]
    )
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    deep, rem, wake = plan.steps

    service.clock.jump_to(deep.starts_at)
    await service._run_stage(plan, deep)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 30

    service.clock.jump_to(rem.starts_at)
    await service._run_stage(plan, rem)
    assert service.unit.mode.is_cooling, "the bed has to come down, so the unit has to cool"
    assert service.unit.target == 25

    # And back up again for the last half hour, which only warming can do.
    service.clock.jump_to(wake.starts_at)
    await service._run_stage(plan, wake)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 26


@pytest.mark.asyncio
async def test_setting_a_lower_temperature_by_hand_cools_too(service):
    """The Now control has no stage before it, so it goes on the last target we
    set. Not a reading, but it is the number we last asked the unit to hold."""
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()

    await service.set_temperature(30)
    assert service.unit.mode is Mode.WARMING

    await service.set_temperature(25)
    assert service.unit.mode.is_cooling
    assert service.unit.target == 25


@pytest.mark.asyncio
async def test_setting_a_higher_temperature_by_hand_still_warms(service):
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()

    await service.set_temperature(20)
    assert service.unit.mode.is_cooling

    await service.set_temperature(28)
    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 28


# --- Reaching for the temperature in the middle of the night --------------------
#
# A change made at 2am is about tonight. It might be the answer to "this stage is
# wrong" for good, but it might equally be one cold Tuesday, and the app cannot
# tell the two apart from a tap on a plus button.
#
# So it used to guess, and it guessed "for good" every time. Every experiment cost
# a permanent change, silently, and undoing it meant remembering what the number
# had been. It writes to tonight now, and making it permanent is a separate tap
# that says so.


def _running(service, stage: Stage, temp: int) -> None:
    """Put the service in the state the poll loop would leave it in mid-stage."""
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()
    service._set_state(power=Power.ON, current_stage=stage, assumed_target_c=temp)


@pytest.mark.asyncio
async def test_a_change_during_a_stage_is_for_tonight(service):
    service.schedule = _schedule()  # deep 17, rem 20, wake 26
    _running(service, Stage.DEEP, 17)

    await service.set_temperature(22)

    assert [s.temp_c for s in service.tonight_now().stages] == [22, 20, 26], "tonight moves"
    assert [s.temp_c for s in service.schedule.stages] == [17, 20, 26], "the routine does not"


@pytest.mark.asyncio
async def test_it_says_it_was_only_for_tonight(service):
    service.schedule = _schedule()
    _running(service, Stage.REM, 20)

    await service.set_temperature(24)

    messages = [e.message for e in service.events.recent(50)]
    assert any("REM is 24C tonight" in m for m in messages)
    assert any("usual REM is untouched" in m for m in messages), "and says the routine is safe"


@pytest.mark.asyncio
async def test_tonights_change_survives_a_restart(service):
    """The Pi restarts routinely. Losing a 2am correction at 3am would put the
    bed back to a number somebody had already rejected."""
    service.schedule = _schedule()
    _running(service, Stage.DEEP, 17)
    await service.set_temperature(22)

    stored = service.db.load_tonight(service._tonight_date())
    assert stored is not None
    assert [s.temp_c for s in stored.stages] == [22, 20, 26]
    assert [s.temp_c for s in service.db.load_schedule().stages] == [17, 20, 26]


@pytest.mark.asyncio
async def test_making_it_permanent_is_a_separate_deliberate_thing(service):
    """The old behaviour, still available, now only when it is asked for."""
    service.schedule = _schedule()
    _running(service, Stage.DEEP, 17)
    await service.set_temperature(22)

    service._adopt_into_running_stage(Stage.DEEP, 22)

    assert [s.temp_c for s in service.db.load_schedule().stages] == [22, 20, 26]


@pytest.mark.asyncio
async def test_a_change_with_no_stage_running_is_left_as_a_one_off(service):
    """Adjusting it in the afternoon says nothing about tonight's schedule."""
    service.schedule = _schedule()
    service.unit.powered = True
    service.unit.powered_at = service.clock.now()
    service._set_state(power=Power.ON, current_stage=None, assumed_target_c=17)

    await service.set_temperature(22)

    assert [s.temp_c for s in service.schedule.stages] == [17, 20, 26]


@pytest.mark.asyncio
async def test_setting_it_to_what_it_already_is_changes_nothing(service):
    service.schedule = _schedule()
    _running(service, Stage.DEEP, 17)
    before = service.schedule.updated_at

    await service.set_temperature(17)

    assert service.schedule.updated_at == before, "no write, and nothing in the log"
    assert not any("changed from" in e.message for e in service.events.recent(50))


@pytest.mark.asyncio
async def test_a_correction_downwards_cools_at_the_night_speed(service):
    """Not Turbo. Pre-conditioning can be loud because nobody is in the bed yet;
    this happens next to a sleeping head."""
    service.schedule = _schedule(cooling_speed=Mode.QUIET)
    _running(service, Stage.WAKE, 26)

    await service.set_temperature(22)

    assert service.unit.mode is Mode.QUIET
    assert service.unit.target == 22


@pytest.mark.asyncio
async def test_a_correction_upwards_into_the_overlap_warms(service):
    service.schedule = _schedule()
    _running(service, Stage.DEEP, 17)

    await service.set_temperature(28)

    assert service.unit.mode is Mode.WARMING
    assert service.unit.target == 28


# --- The bug that would have made all of the above unusable ---------------------


@pytest.mark.asyncio
async def test_editing_mid_night_does_not_report_finished_stages_as_missed(service):
    """update_schedule used to clear the fired marks. Every completed stage then
    looked un-run, and the next tick logged an error about each one. Adjusting
    anything at 2am produced a screen of complaints about the past."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    deep, rem, _ = plan.steps

    # Both stages ran at their boundaries, as they would have.
    for step in (deep, rem):
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)
        service.scheduler.fired.mark(service.scheduler.due(service.schedule, step.starts_at))

    # Two hours into REM, well past every earlier stage's window.
    service.clock.jump_to(rem.starts_at + timedelta(hours=2))
    _running(service, Stage.REM, 20)
    await service.set_temperature(23)

    missed = service.scheduler.missed(service.schedule, service.clock.now())
    assert [j.step.stage for j in missed if j.step] == [], "nothing in the past is missed"


def test_moving_the_wake_time_invalidates_the_marks_by_itself(service):
    """Which is why clearing them was never needed: they are keyed by wake time."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    job = Job("stage", plan, plan.steps[0])
    service.scheduler.fired.mark(job)
    assert service.scheduler.fired.has_fired(job)

    service.update_schedule({"wake_time": time(7, 30)})
    moved = service.schedule.plan_for(datetime(2026, 9, 8).date())
    assert not service.scheduler.fired.has_fired(Job("stage", moved, moved.steps[0]))


@pytest.mark.asyncio
async def test_any_schedule_edit_mid_night_leaves_the_past_alone(service):
    """Not just a temperature correction. Changing the days, or a duration, or
    anything else at 2am used to have the same effect."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    deep, rem, _ = plan.steps
    for step in (deep, rem):
        service.clock.jump_to(step.starts_at)
        await service._run_stage(plan, step)
        service.scheduler.fired.mark(service.scheduler.due(service.schedule, step.starts_at))

    service.clock.jump_to(rem.starts_at + timedelta(hours=2))
    service.update_schedule({"name": "Renamed at 2am"})

    missed = service.scheduler.missed(service.schedule, service.clock.now())
    assert missed == []


# --- Not being able to see the plug at a stage boundary -------------------------


class Unreachable:
    """A plug that never answers, which is what a dropped read looks like."""

    async def read_watts(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_an_unreachable_plug_at_a_boundary_does_not_press_power(service):
    """Power is a toggle. Pressing it without knowing risks switching off a unit
    that is running, which costs the rest of the night. A stage that does not
    land costs one stretch of it. So it sets the temperature and leaves power be."""
    service.power = Unreachable()
    service.commands.power = service.power
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    step = plan.steps[0]

    service.clock.jump_to(step.starts_at)
    await service._run_stage(plan, step)

    sent = " ".join(service.transmitter.lines)
    assert "power_on()" not in sent
    assert "-> power" not in sent, "no power press at all"


@pytest.mark.asyncio
async def test_it_says_it_could_not_confirm_rather_than_going_quiet(service):
    """The bug this fixes. It already did the right thing and said nothing, so a
    night driven blind looked identical to a night that went perfectly."""
    service.power = Unreachable()
    service.commands.power = service.power
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    step = plan.steps[0]

    service.clock.jump_to(step.starts_at)
    await service._run_stage(plan, step)

    warnings = [e.message for e in service.events.recent(30) if e.level == "warning"]
    assert any("Could not reach the plug" in m for m in warnings)
    assert any("switching off a running unit" in m for m in warnings)


@pytest.mark.asyncio
async def test_a_plug_that_says_off_still_powers_on(service):
    """The case that works, unchanged: a confirmed reading below the off line."""
    service.schedule = _schedule()
    plan = service.schedule.plan_for(datetime(2026, 9, 8).date())
    step = plan.steps[0]

    service.clock.jump_to(step.starts_at)
    await service._run_stage(plan, step)

    assert "power_on()" in " ".join(service.transmitter.lines)
    assert service.unit.powered


def test_a_fake_transmitter_with_a_real_plug_is_called_out(tmp_path):
    """The half-real setup watches two different objects: the presses drive the
    simulation, the plug measures the bedroom. Every power check is then about
    the wrong unit, and a simulated night looks broken rather than mismatched."""
    settings = Settings(
        db_path=str(tmp_path / "half.db"), power_monitor="shelly", transmitter="fake"
    )
    clock = VirtualClock(datetime(2026, 9, 7, 21, 0))
    svc = Service(settings, clock=clock, echo=False)
    try:
        asyncio.run(svc.start())
        messages = [e.message for e in svc.events.recent(20)]
        assert any("measuring the real one" in m for m in messages)
        assert any("HS_POWER_MONITOR=fake" in m for m in messages)
    finally:
        asyncio.run(svc.stop())



# --- Where the bed actually starts from ----------------------------------------
#
# This was an assumed 20C bedroom until the hose probes went on. The number was
# close most nights and wrong on the nights it mattered, because a cold room is
# exactly when the head start needs to be longer.


def test_the_head_start_is_worked_out_from_the_measured_bed(service):
    """A colder bed is a longer job. That is the whole point of measuring it."""
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 28)])

    assumed = schedule.preconditioning()
    cold = schedule.preconditioning(15.5)
    warm = schedule.preconditioning(23.0)

    assert cold.lead_minutes > assumed.lead_minutes > warm.lead_minutes


def test_a_measured_start_says_so_and_an_assumed_one_says_so_too(service):
    """The project's oldest rule: never show a value that has not been confirmed.

    Both of these are printed on the same card in the same place, so the words
    are the only thing telling them apart.
    """
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 28)])

    assert "on the hoses" in schedule.preconditioning(19.7).reason
    assert "19.7C" in schedule.preconditioning(19.7).reason

    assumed = schedule.preconditioning().reason
    assert "about 20C" in assumed
    assert "hoses" not in assumed


def test_a_measured_bed_can_change_which_way_the_unit_has_to_go(service):
    """Not just the timing. A first stage of 22C is warming from a 19C bed and
    cooling from a 26C one, and assuming 20C gets the second of those wrong."""
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 22)])

    assert schedule.preconditioning(26.0).mode is Mode.TURBO
    # Warming mode only goes down to 25C, so from below there is nothing it can
    # do. It says so rather than running the cooler at a bed that needs heat.
    assert schedule.preconditioning(19.0).mode is None


def test_quiet_probes_leave_the_head_start_exactly_where_it_was(service):
    """A probe board on a bedroom Wi-Fi link cannot be allowed to change a night
    by going quiet. Without a reading this is the behaviour it has always had."""
    schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 28)])
    quiet = schedule.preconditioning(None)
    assert quiet.mode is Mode.WARMING
    assert quiet.lead_minutes == schedule.preconditioning(20.0).lead_minutes


def test_the_scheduler_uses_the_reading_and_not_only_the_card(service):
    """The number on screen and the moment the unit switches on are the same
    calculation. Showing one and running the other is the one disagreement this
    project cannot have."""
    service.schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 28)])
    now = service.clock.now()

    warm = service.scheduler.plan_in_progress(service.schedule, now)
    hoses(service, 15.0)
    cold = service.scheduler.plan_in_progress(service.schedule, now)

    assert cold.precool_at < warm.precool_at
    assert cold.bedtime_at == warm.bedtime_at, "only the head start moves"


def test_what_the_app_is_served_carries_the_measurement(service):
    """The card reads this, so it has to be the answer the scheduler is using."""
    service.schedule = _schedule(stages=[SleepStage(Stage.DEEP, 240, 28)])

    assert "about 20C" in service.schedule_as_shown()["preconditioning"]["reason"]

    hoses(service, 15.0)
    shown = service.schedule_as_shown()["preconditioning"]
    assert "15.0C on the hoses" in shown["reason"]
    assert shown["lead_minutes"] == service.schedule.preconditioning(15.0).lead_minutes
