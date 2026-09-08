"""Everything wired together, and the only place that holds state.

One object owns the clock, the adapters, the database, the command sequences and
the tick loop. Commands are serialised behind a lock, because two infrared
sequences interleaving would produce nonsense that no amount of railing could
recover from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable

from .adapters import build_adapters
from .adapters.fake_transmitter import FakeTransmitter
from .clock import Clock, RealClock, SimClock, VirtualClock
from .config import Settings
from .db import Database
from .events import Event, EventLog
from .models import (
    STAGE_LABEL,
    Activity,
    DeviceHealth,
    DeviceState,
    Health,
    Mode,
    NightPlan,
    Power,
    Schedule,
    Stage,
    StageStep,
    mode_for_target,
    range_for,
    rehearsal_plan,
)
from .scheduler import Job, Scheduler
from .sequences import CommandFailed, Commands

log = logging.getLogger(__name__)

#: How long to wait between attempts at a step that did not land.
#:
#: Matched to the blaster's health check, because the usual reason a step fails
#: is that the blaster is off the Wi-Fi, and there is no point trying again
#: before the thing that would tell us it is back has run.
RETRY_AFTER = timedelta(seconds=30)

#: Below this, an idle reading is the unit not having started rather than having
#: arrived. Power on, mode change and rail-and-count take about thirty seconds of
#: infrared before the compressor is doing anything at all.
MIN_PRECONDITION_SECONDS = 120

#: Past this it is not arriving. The cap on a lead time is two hours, so a run
#: still going after that has answered a different question.
MAX_PRECONDITION_SECONDS = 3 * 60 * 60


class Service:
    def __init__(self, settings: Settings, *, clock: Clock | None = None, echo: bool = True) -> None:
        self.settings = settings
        self.clock: Clock = clock or (
            SimClock(speed=settings.sim_speed) if settings.is_simulated else RealClock()
        )
        self.db = Database(settings.db_path)
        self.events = EventLog(self.clock)
        self.transmitter, self.power, self.unit = build_adapters(settings, self.clock, echo=echo)
        self.commands = Commands(self.transmitter, self.power, self.clock, settings, self.events)
        self.scheduler = Scheduler(learned_lead=self._learned_lead)

        self.schedule: Schedule = self.db.load_schedule()
        self.state = DeviceState()
        self.events.seed(self.db.recent_events(200))

        # None means never asked, which is a different thing from "not answering"
        # and the bar says so rather than showing a colour it has not earned.
        # Which job is being retried, and the earliest another attempt is worth
        # making. Keyed by the job rather than a flag so a different step
        # arriving clears it by itself.
        # A pre-conditioning run in flight: when it started, and what it was
        # aiming at. The plug says when it got there.
        self._precondition: tuple[datetime, Mode, int] | None = None

        self._retrying: str | None = None
        self._retry_after: datetime | None = None

        self._plug_ok: bool | None = None
        self._plug_ok_at: datetime | None = None
        self._blaster_ok: bool | None = None
        self._blaster_ok_at: datetime | None = None

        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._unsubscribe_events: Callable[[], None] | None = None

    # --- Lifecycle ------------------------------------------------------------

    # --- Health ---------------------------------------------------------------
    #
    # Presses are hours apart and the plug is read every thirty seconds, so
    # without asking, a blaster that fell off the Wi-Fi at midnight would look
    # perfectly fine right up until the stage that needed it. Both get asked, and
    # both remember when they last answered so a wobble reads differently from a
    # device that has gone.

    def health(self) -> list[DeviceHealth]:
        now = self.clock.now()
        real_plug = self.settings.power_monitor != "fake"
        real_blaster = self.settings.transmitter != "fake"

        plug = (
            DeviceHealth.judge(
                "plug",
                now=now,
                last_ok_at=self._plug_ok_at,
                ok_now=self._plug_ok,
                where=self.settings.shelly_host,
                note=f"Reading {self.state.observed_power_w:.1f} W"
                if self.state.observed_power_w is not None
                else "",
            )
            if real_plug
            else DeviceHealth("plug", Health.SIMULATED, "No plug. Watts are invented")
        )

        blaster = (
            DeviceHealth.judge(
                "blaster",
                now=now,
                last_ok_at=self._blaster_ok_at,
                ok_now=self._blaster_ok,
                where=self.settings.esphome_host,
                note=f"All eight buttons ready at {self.settings.esphome_host}",
            )
            if real_blaster
            else DeviceHealth("blaster", Health.SIMULATED, "No blaster. Presses are printed")
        )
        return [plug, blaster]

    async def _check_blaster(self) -> None:
        ok = await self.transmitter.reachable()
        was = self._blaster_ok
        self._blaster_ok = ok
        if ok:
            self._blaster_ok_at = self.clock.now()
        # Said once on the way down and once on the way back, not every check.
        if was is not None and was != ok:
            if ok:
                self.events.info("blaster", "The blaster is answering again")
            else:
                self.events.warning(
                    "blaster",
                    f"The blaster at {self.settings.esphome_host} is not answering. "
                    "Presses will not reach the unit until it does.",
                )
        self._push_state()

    async def _health_loop(self) -> None:
        while True:
            try:
                await self._check_blaster()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("blaster health check failed")
            await self.clock.sleep(self.settings.power_sample_seconds)

    async def start(self) -> None:
        self._unsubscribe_events = self.events.subscribe(self._on_event)
        self.events.info("service", f"Started with a {self.settings.transmitter} transmitter")

        # A fake transmitter with a real plug is a useful half-step, but the two
        # halves are then watching different objects: the presses drive the
        # simulation and the plug measures the unit in the bedroom. Every check
        # that asks the plug about the unit is meaningless, which looks exactly
        # like a broken night rather than a mismatched setup.
        if self.settings.transmitter == "fake" and self.settings.power_monitor != "fake":
            self.events.warning(
                "service",
                "The presses go to the simulated unit but the plug is measuring the real one, "
                "so a simulated night will not behave: the power checks are about a different "
                "unit from the one being driven. Set HS_POWER_MONITOR=fake to simulate a whole "
                "night, or HS_TRANSMITTER=esphome once the blaster is captured.",
            )
        await self._sample_power()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="scheduler"),
            asyncio.create_task(self._power_loop(), name="power"),
            asyncio.create_task(self._health_loop(), name="health"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._unsubscribe_events:
            self._unsubscribe_events()
        await self.transmitter.close()
        await self.power.close()
        self.db.close()

    # --- Live updates ---------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A phone that has stopped reading is not worth blocking the
                # scheduler for. It will refetch when it comes back.
                self._subscribers.discard(queue)

    def _on_event(self, event: Event) -> None:
        self.db.add_event(event)
        self._broadcast({"event": event.as_dict()})

    def _push_state(self) -> None:
        from .api.schemas import health_json, state_json

        # Health goes with it rather than on a poll of its own. It changes for
        # the same reasons state does, and a device bar that lags behind the
        # thing it is describing is worse than not having one.
        self._broadcast(
            {"state": state_json(self.state), "health": health_json(self.health())}
        )

    def _push_schedule(self) -> None:
        from .api.schemas import schedule_json

        self._broadcast({"schedule": schedule_json(self.schedule)})

    # --- Loops ----------------------------------------------------------------

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("scheduler tick failed")
            await self.clock.sleep(1)

    async def _tick(self) -> None:
        now = self.clock.now()

        # A missed stage means the bed spent that stretch of the night at the
        # wrong temperature. Worth saying out loud rather than passing over.
        for job in self.scheduler.missed(self.schedule, now):
            self.scheduler.fired.mark(job)
            assert job.step is not None
            self.events.error(
                "stage",
                f"Missed the {job.step.label} stage at {job.step.starts_at:%H:%M}. "
                f"The bed stayed where it was instead of going to {job.step.temp_c}C.",
            )

        job = self.scheduler.due(self.schedule, now)
        if job is None:
            return

        # Backing off between attempts. Without it a blaster that is not
        # answering would be retried every second, which floods the log and gets
        # nowhere: whatever is wrong takes longer than a second to fix itself.
        if self._retry_after is not None and now < self._retry_after and job.key == self._retrying:
            return

        first_try = job.key != self._retrying
        if job.kind == "stage" and first_try:
            self._report_stage_start(job)

        if await self._run_job(job):
            # Marked only once it has actually worked. Marking before running is
            # what made a failed stage permanent: the job was recorded as done,
            # due() never offered it again, and the bed sat at the wrong
            # temperature for the rest of that stage with nothing left to try.
            self.scheduler.fired.mark(job)
            if not first_try:
                self.events.info("stage", f"The {job.key} step landed on a retry.")
            self._retrying = None
            self._retry_after = None
            return

        # Left unmarked on purpose, so due() offers it again. The stage's own
        # window is the limit: once it closes, missed() reports it and stops.
        if first_try:
            self.events.warning(
                "stage",
                f"The {job.key} step did not land. Retrying every "
                f"{int(RETRY_AFTER.total_seconds())}s until its window closes.",
            )
        self._retrying = job.key
        self._retry_after = now + RETRY_AFTER

    def _report_stage_start(self, job: Job) -> None:
        """The first stage of a night is also the moment to check pre-conditioning
        actually did something."""
        if job.step is not None and job.plan.steps and job.step is job.plan.steps[0]:
            self._report_idle_preconditioning(job.plan)

    async def _power_loop(self) -> None:
        while True:
            await self.clock.sleep(self.settings.power_sample_seconds)
            try:
                await self._sample_power()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("power sample failed")

    async def _sample_power(self) -> None:
        watts = await self.power.read_watts()
        now = self.clock.now()

        self._watch_precondition(watts, now)

        # Every read is already a reachability check, so there is nothing extra
        # to ask: a reading means the plug answered.
        was = self._plug_ok
        self._plug_ok = watts is not None
        if watts is not None:
            self._plug_ok_at = now
        # _set_state below only pushes when the state itself changed, and a plug
        # that has stopped answering leaves the state exactly as it was. Without
        # this the bar would keep showing green on a dead plug.
        plug_changed = was is not None and was != self._plug_ok
        if plug_changed:
            self._push_state()
        activity = self.settings.thresholds.classify(watts)

        # The plug is the only thing here that is actually observed, so it
        # overrules what we believed about power. Everything else stays a belief.
        power = self.state.power
        if watts is not None:
            power = Power.OFF if watts < self.settings.off_threshold_w else Power.ON

        step = self.scheduler.stage_now(self.schedule, now)
        self._set_state(
            observed_power_w=watts,
            inferred_activity=activity,
            power=power,
            current_stage=step.stage if step and power is Power.ON else None,
        )
        if watts is not None:
            self.db.add_power_sample(now, watts)
            if now.minute == 0 and now.second < self.settings.power_sample_seconds:
                self.db.prune_power(now - timedelta(days=7))

    # --- Nightly jobs ---------------------------------------------------------

    # --- Rehearsal ------------------------------------------------------------

    async def start_rehearsal(self, seconds: int) -> NightPlan:
        """Run tonight's night, compressed, right now.

        The point is to answer one question before trusting the thing to run
        unattended: does every stage boundary actually land on the real unit, in
        the right order, at the right temperature, in the right mode. Reading the
        code cannot answer it and neither can the simulator, because the only
        part that has never been exercised is the infrared arriving at a unit
        that is really there.

        It is not a special code path. It builds a NightPlan with short stages
        and hands it to the same scheduler, so what runs is the same due(), the
        same fired marks, the same power checks and the same sequences that will
        run at 2am. Only the durations differ.
        """
        if not self.schedule.stages:
            raise ValueError("There are no stages to rehearse.")

        plan = rehearsal_plan(
            self.schedule.stages,
            self.schedule.cooling_speed,
            now=self.clock.now(),
            total_seconds=seconds,
        )
        # A fresh set of marks, so a second rehearsal is not skipped as one that
        # has already fired. The real night's marks are keyed by its own wake
        # time, so they survive this untouched.
        self.scheduler.fired.clear()
        self.scheduler.rehearsal = plan
        self._set_state(rehearsal_ends_at=plan.wake_at)

        names = ", ".join(f"{s.label} {s.temp_c}C {s.mode.value}" for s in plan.steps)
        self.events.info(
            "rehearsal",
            f"Rehearsing the whole night in {round((plan.wake_at - self.clock.now()).total_seconds())}s: "
            f"{names}, then off. Real presses, real plug checks, only the clock is generous.",
        )
        return plan

    async def stop_rehearsal(self, *, power_off: bool = True) -> None:
        """End it early, and leave the unit off rather than running.

        Stopping has to be safe on its own. Whatever the rehearsal was part way
        through, the honest end state is the same one the night would have
        reached, which is off.
        """
        if self.scheduler.rehearsal is None:
            return
        self.scheduler.rehearsal = None
        self.scheduler.fired.clear()
        self._set_state(rehearsal_ends_at=None, current_stage=None)
        self.events.info("rehearsal", "Rehearsal stopped.")
        if power_off:
            await self.power_off()

    def _watch_precondition(self, watts: float | None, now: datetime) -> None:
        """Time how long the bed really takes, using the only honest sensor here.

        A unit working towards a setpoint draws 170 W cooling or 300 W heating.
        When it gets there it settles into the idle band. That fall is the answer
        to the question the lead time has always been guessing at, and it costs
        nothing to watch because the plug is already being read every thirty
        seconds for other reasons.
        """
        if self._precondition is None or watts is None:
            return
        started, mode, target = self._precondition
        elapsed = int((now - started).total_seconds())
        activity = self.settings.thresholds.classify(watts)

        if activity is Activity.IDLE and elapsed >= MIN_PRECONDITION_SECONDS:
            self._precondition = None
            self.db.record_precondition(started, mode.value, target, elapsed, True)
            self.events.info(
                "precool",
                f"The bed reached {target}C in {elapsed // 60}m {elapsed % 60}s. "
                "Measured off the plug, and used to time the next one.",
            )
            return

        # Ran the whole way and never settled. Kept rather than discarded: it
        # means the target was not reachable that night, which is worth more than
        # the timing would have been.
        if elapsed > MAX_PRECONDITION_SECONDS:
            self._precondition = None
            self.db.record_precondition(started, mode.value, target, elapsed, False)
            self.events.warning(
                "precool",
                f"Ran for {elapsed // 60} minutes without settling at {target}C. The unit is "
                "working flat out and not getting there, so that target may not be reachable "
                "in this room.",
            )

    def _learned_lead(self, mode: Mode, target_c: int) -> int | None:
        return self.db.learned_lead_minutes(mode.value, target_c)

    async def _run_job(self, job: Job) -> bool:
        if job.kind == "precool":
            return await self._run_precool(job.plan)
        if job.kind == "stage" and job.step is not None:
            return await self._run_stage(job.plan, job.step)
        if job.kind == "power_off":
            ok = await self._run_power_off(job.plan)
            # A rehearsal ends when its night does, whether the power off worked
            # or not. Leaving it in place would hold the real schedule out for
            # the whole two hour grace window afterwards.
            if self.scheduler.rehearsal is job.plan:
                self.scheduler.rehearsal = None
                self._set_state(rehearsal_ends_at=None, current_stage=None)
                self.events.info("rehearsal", "Rehearsal finished. Back on the real schedule.")
            return ok
        return True

    async def _run_precool(self, plan: NightPlan) -> bool:
        # Nothing is chosen here. The plan already worked out which way the bed has
        # to move, whether the unit can move it, and how long that needs.
        pre = plan.preconditioning
        if pre.mode is None:
            self.events.info("precool", pre.reason)
            return True

        target = self.schedule.first_temp_c
        verb = "Pre-heating" if pre.mode is Mode.WARMING else "Pre-cooling"
        self.events.info(
            "precool",
            f"{verb} in {pre.mode.value} to {target}C, starting {pre.lead_minutes} minutes "
            f"before a {plan.bedtime_at:%H:%M} bedtime and a {plan.wake_at:%a %H:%M} wake",
        )
        mode = pre.mode
        async with self._lock:
            try:
                await self.commands.power_on()
                self._set_state(power=Power.ON, current_stage=None)
                await self._apply(mode, target)
                # From here the plug is timing it. The clock starts once the
                # presses have landed, not when the job fired, because the thirty
                # seconds of infrared is not the bed cooling.
                self._precondition = (self.clock.now(), mode, target)
                return True
            except CommandFailed as exc:
                self._fail("precool", exc)
                return False

    async def _run_stage(self, plan: NightPlan, step: StageStep) -> bool:
        self.events.info(
            "stage",
            f"{step.label}: {step.temp_c}C in {step.mode.value} until {step.ends_at:%H:%M}",
        )
        async with self._lock:
            try:
                # The unit may have switched itself off, or never been on if the
                # pre-conditioning step was skipped.
                watts = await self.power.read_watts()
                if watts is None:
                    # Deliberately not pressing power. It is a toggle: if the unit
                    # is actually on, that press turns the bed off for the rest of
                    # the night, which is far worse than a stage that does not
                    # land. So set the temperature blind and say so.
                    self.events.warning(
                        "stage",
                        "Could not reach the plug, so whether the unit is on is unknown. Setting "
                        f"{step.temp_c}C anyway. Pressing power without knowing would risk "
                        "switching off a running unit, so it is left alone.",
                    )
                elif watts < self.settings.off_threshold_w:
                    self.events.warning("stage", "Unit was off at a stage boundary, powering on")
                    await self.commands.power_on()
                    self._set_state(power=Power.ON)
                await self._apply(step.mode, step.temp_c)
                return True
            except CommandFailed as exc:
                self._fail("stage", exc)
                return False

    async def _run_power_off(self, plan: NightPlan) -> bool:
        """Not optional. Without the unit's own schedule, nothing else does this.

        The unit's twelve hour inactivity cutoff is reset by every stage
        transition, so it will not save us. If this fails, the only thing left
        standing between a dead Pi and a bed running all day is the Shelly's own
        auto-off timer.
        """
        self.events.info("power_off", f"Night finished at {plan.wake_at:%H:%M}, switching off")
        async with self._lock:
            try:
                await self.commands.power_off()
                self._set_state(
                    power=Power.OFF, assumed_target_c=None, last_command_at=self.clock.now()
                )
                return True
            except CommandFailed as exc:
                self.events.error(
                    "power_off",
                    f"{exc} The unit will not switch itself off. Check it, and check the "
                    "Shelly's own auto-off timer is set.",
                )
                self._set_state(last_error=str(exc), power=Power.UNKNOWN)

    async def _apply(self, mode: Mode, target_c: int) -> None:
        """Put the unit into a mode at a temperature. The whole night is this."""
        if self.state.assumed_mode is not mode:
            await self.commands.set_mode(mode)
            self._set_state(assumed_mode=mode)
        await self.commands.set_temperature(target_c, mode)
        self._set_state(assumed_target_c=target_c, last_command_at=self.clock.now())

    def _report_idle_preconditioning(self, plan: NightPlan) -> None:
        """Say so when the pre-conditioning run never actually did anything.

        The plug is the only real sensor here, and it can answer this. If the draw
        never rose above idle between pre-conditioning starting and bedtime, the
        unit was never working, which means the bed was already past the target.
        """
        if plan.precool_at is None:
            return
        samples = self.db.power_history(plan.precool_at)
        if not samples:
            return
        peak = max(watts for _, watts in samples)
        if peak >= self.settings.idle_max_w:
            return

        wanted = "warm" if plan.preconditioning.mode is Mode.WARMING else "cool"
        self.events.warning(
            "precool",
            f"The unit never drew more than {peak:.0f} W while pre-conditioning, so it was "
            f"not working. The bed was most likely already past {self.schedule.first_temp_c}C, "
            f"and it cannot {wanted} in the other direction.",
        )

    # --- Commands from the app ------------------------------------------------

    async def power_on(self) -> None:
        async with self._lock:
            try:
                await self.commands.power_on()
                self._set_state(power=Power.ON, last_command_at=self.clock.now())
            except CommandFailed as exc:
                self._fail("power", exc, power=Power.UNKNOWN)

    async def power_off(self) -> None:
        async with self._lock:
            try:
                await self.commands.power_off()
                self._set_state(
                    power=Power.OFF,
                    current_stage=None,
                    assumed_target_c=None,
                    last_command_at=self.clock.now(),
                )
            except CommandFailed as exc:
                self._fail("power", exc, power=Power.UNKNOWN)

    def mode_for_now(self, target_c: int) -> Mode:
        """Which mode a temperature set by hand should land in.

        Between 25C and 35C both modes reach the number and the direction of
        travel decides, so it needs somewhere to come from. The last target we
        set is the best evidence of where the bed is: it is not a reading, but it
        is the number we last asked the unit to hold.
        """
        return mode_for_target(
            target_c,
            self.schedule.cooling_speed,
            coming_from_c=self.state.assumed_target_c,
            coming_from_mode=self.state.assumed_mode,
        )

    async def set_temperature(self, target_c: int) -> None:
        """Set the temperature now, by hand.

        Which mode that needs is worked out the same way a stage works it out,
        from the direction the bed has to travel. Below 25C only cooling can
        express it, above 35C only warming can, and in between the direction
        decides. A cooling correction uses the night's own speed, which is Quiet
        unless it has been changed, because this happens next to a sleeping head.
        """
        mode = self.mode_for_now(target_c)
        stage = self.state.current_stage
        async with self._lock:
            try:
                if self.state.assumed_mode is not mode:
                    await self.commands.set_mode(mode)
                    self._set_state(assumed_mode=mode)
                await self.commands.set_temperature(target_c, mode)
                self._set_state(assumed_target_c=target_c, last_command_at=self.clock.now())
            except CommandFailed as exc:
                self._fail("temperature", exc, assumed_target_c=None)
                raise

        # Outside the lock: the presses have landed, and this is bookkeeping.
        if stage is not None:
            self._adopt_into_running_stage(stage, target_c)

    async def mute(self) -> None:
        """Toggle the unit's button beep.

        Never automatic. The unit remembers this setting across power cycles, so
        firing it on every power on would unmute it every other night, and the
        press that unmuted it would beep. It is a one-time setup action, done from
        the app once the codes are captured.
        """
        async with self._lock:
            try:
                await self.commands.mute()
                # The wake preamble is two temp_down presses, and whichever of
                # them are not swallowed really do lower the target: the press log
                # shows 17C going to 15C. Every other command rails and counts
                # afterwards, which absorbs that; this one has nothing to count
                # to. So the target stops being something we know, and the app
                # says so rather than carrying on showing a number the unit no
                # longer holds. The next stage boundary sets it properly.
                self._set_state(assumed_target_c=None, last_command_at=self.clock.now())
            except CommandFailed as exc:
                self._fail("mute", exc)

    async def set_mode(self, mode: Mode) -> None:
        async with self._lock:
            try:
                await self.commands.set_mode(mode)
                low, high = range_for(mode)
                target = self.state.assumed_target_c
                self._set_state(
                    assumed_mode=mode,
                    # Each mode remembers its own temperature, so a target outside
                    # the new mode's range is no longer meaningful.
                    assumed_target_c=target if target and low <= target <= high else None,
                    last_command_at=self.clock.now(),
                )
            except CommandFailed as exc:
                self._fail("mode", exc, assumed_mode=None)
                raise

    def update_schedule(self, patch: dict[str, Any]) -> Schedule:
        """Save a change to the schedule. Deliberately does not touch the unit.

        It also deliberately does not clear the fired marks, which it used to.
        They are keyed by the plan's wake time already, so moving the wake time
        invalidates them by itself and clearing was never needed. What clearing
        did do was make every completed stage look un-run, so the next tick
        reported "Missed the Deep stage" for a stage that had gone perfectly.
        Editing anything at 2am produced a screen of errors about the past.
        """
        self.schedule = replace(self.schedule, **patch, updated_at=self.clock.now())
        self.db.save_schedule(self.schedule)
        self._push_schedule()
        return self.schedule

    def _adopt_into_running_stage(self, stage: Stage, target_c: int) -> None:
        """Remember a correction made in the middle of the night.

        Reaching for the temperature at 2am is not a one-off. It is the answer to
        "this stage is wrong", and the stage will be just as wrong tomorrow unless
        something is done about it. So the schedule takes the new number and says
        so. Changing it back is one tap on that stage's tab.
        """
        current = next((s for s in self.schedule.stages if s.stage is stage), None)
        if current is None or current.temp_c == target_c:
            return

        was = current.temp_c
        self.update_schedule(
            {
                "stages": [
                    replace(s, temp_c=target_c) if s.stage is stage else s
                    for s in self.schedule.stages
                ]
            }
        )
        label = STAGE_LABEL[stage]
        self.events.info(
            "stage",
            f"{label} changed from {was}C to {target_c}C while it was running, so {label} "
            f"is {target_c}C from now on. Change it back on the {label} tab.",
        )

    # --- State ----------------------------------------------------------------

    def _set_state(self, **patch: Any) -> None:
        before = self.state
        self.state = replace(self.state, **patch)
        if self.state != before:
            self._push_state()

    def _fail(self, kind: str, exc: CommandFailed, **patch: Any) -> None:
        """Verification failed. Say so plainly and do not silently retry."""
        self.events.error(kind, str(exc))
        self._set_state(last_error=str(exc), **patch)

    # --- Simulation -----------------------------------------------------------

    @property
    def is_simulated(self) -> bool:
        return self.unit is not None

    def sim_snapshot(self) -> dict[str, Any] | None:
        if self.unit is None:
            return None
        lines = list(self.transmitter.lines) if isinstance(self.transmitter, FakeTransmitter) else []
        return {
            "now": self.clock.now().isoformat(),
            "speed": getattr(self.clock, "speed", 1.0),
            "unit": self.unit.snapshot(),
            "press_log": lines[-120:],
        }

    def sim_jump_to(self, target: datetime) -> None:
        if isinstance(self.clock, (SimClock, VirtualClock)):
            self.clock.jump_to(target)
            # A jump lands in a different night, so nothing that fired before
            # should count as fired now.
            self.scheduler.fired = type(self.scheduler.fired)()
            self.events.info("sim", f"Jumped the clock to {target:%a %d %b %H:%M}")

    def sim_set_speed(self, speed: float) -> None:
        from .clock import MAX_SIM_SPEED

        if isinstance(self.clock, SimClock):
            capped = min(max(speed, 0.1), MAX_SIM_SPEED)
            self.clock.set_speed(capped)
            self.events.info("sim", f"Clock speed set to {capped:g}x")


_ = Activity
