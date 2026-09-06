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
    Activity,
    DeviceState,
    Mode,
    NightPlan,
    Power,
    Precondition,
    Schedule,
    Stage,
    StageStep,
    mode_for_target,
    range_for,
)
from .scheduler import Job, Scheduler
from .sequences import CommandFailed, Commands

log = logging.getLogger(__name__)


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
        self.scheduler = Scheduler()

        self.schedule: Schedule = self.db.load_schedule()
        self.state = DeviceState()
        self.events.seed(self.db.recent_events(200))

        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._unsubscribe_events: Callable[[], None] | None = None

    # --- Lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        self._unsubscribe_events = self.events.subscribe(self._on_event)
        self.events.info("service", f"Started with a {self.settings.transmitter} transmitter")
        await self._sample_power()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="scheduler"),
            asyncio.create_task(self._power_loop(), name="power"),
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
        from .api.schemas import state_json

        self._broadcast({"state": state_json(self.state)})

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
        self.scheduler.fired.mark(job)
        if job.kind == "stage":
            self._report_stage_start(job)
        await self._run_job(job)

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

    async def _run_job(self, job: Job) -> None:
        if job.kind == "precool":
            await self._run_precool(job.plan)
        elif job.kind == "stage" and job.step is not None:
            await self._run_stage(job.plan, job.step)
        elif job.kind == "power_off":
            await self._run_power_off(job.plan)

    async def _run_precool(self, plan: NightPlan) -> None:
        # A cooler cannot warm a bed. When the first stage is above whatever the
        # bed is resting at, only warming mode gets there, and only down to 25C.
        problem = self.schedule.precondition_problem()
        if problem is not None:
            self.events.warning("precool", f"{problem} Pre-cooling instead.")

        target = self.schedule.first_temp_c
        mode = (
            self.schedule.precondition_mode
            if problem is None
            else Precondition.COOL.mode_for(self.schedule.cooling_speed)
        )
        verb = "Pre-heating" if mode is Mode.WARMING else "Pre-cooling"
        self.events.info(
            "precool",
            f"{verb} in {mode.value} to {target}C, for a {plan.bedtime_at:%H:%M} bedtime "
            f"and a {plan.wake_at:%a %H:%M} wake",
        )
        async with self._lock:
            try:
                await self.commands.power_on()
                self._set_state(power=Power.ON, current_stage=None)
                # Roughly thirty presses land at each stage boundary through the
                # night, and the unit beeps on every one of them.
                await self.commands.mute()
                await self._apply(mode, target)
            except CommandFailed as exc:
                self._fail("precool", exc)

    async def _run_stage(self, plan: NightPlan, step: StageStep) -> None:
        self.events.info(
            "stage",
            f"{step.label}: {step.temp_c}C in {step.mode.value} until {step.ends_at:%H:%M}",
        )
        async with self._lock:
            try:
                # The unit may have switched itself off, or never been on if the
                # pre-conditioning step was skipped.
                watts = await self.power.read_watts()
                if watts is not None and watts < self.settings.off_threshold_w:
                    self.events.warning("stage", "Unit was off at a stage boundary, powering on")
                    await self.commands.power_on()
                    self._set_state(power=Power.ON)
                    await self.commands.mute()
                await self._apply(step.mode, step.temp_c)
            except CommandFailed as exc:
                self._fail("stage", exc)

    async def _run_power_off(self, plan: NightPlan) -> None:
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

        wanted = "warm" if self.schedule.precondition_mode is Mode.WARMING else "cool"
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

    async def set_temperature(self, target_c: int) -> None:
        # Below 25C has to cool, at or above 25C it warms. The unit switches
        # freely now that its own scheduler is never armed.
        mode = mode_for_target(target_c, self.schedule.cooling_speed)
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
        self.schedule = replace(self.schedule, **patch, updated_at=self.clock.now())
        self.db.save_schedule(self.schedule)
        # A changed wake time means tonight's jobs are a different night now.
        self.scheduler.fired = type(self.scheduler.fired)()
        self._push_schedule()
        return self.schedule

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
