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
    PRECOOL_MODE,
    Activity,
    DeviceState,
    Mode,
    NightPlan,
    Power,
    Schedule,
    Tristate,
    range_for,
)
from .scheduler import Scheduler
from .sequences import CommandFailed, Commands, WriteProgress

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
        missed = self.scheduler.missed_arming(self.schedule, now)
        if missed is not None:
            self.scheduler.fired.mark("arm", missed)
            self.events.error(
                "schedule_arm",
                f"Missed the arming window for {missed.wake_at:%a %H:%M}. Not arming late, "
                "because the schedule would then run past the wake time.",
            )
            self._set_state(in_schedule=Tristate.UNKNOWN)

        due = self.scheduler.due(self.schedule, now)
        if due is None:
            return
        job, plan = due
        self.scheduler.fired.mark(job, plan)
        handler = {
            "precool": self._run_precool,
            "arm": self._run_arm,
            "wake_check": self._run_wake_check,
        }[job]
        await handler(plan)

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

        in_schedule = self.state.in_schedule
        if power is Power.OFF and in_schedule is Tristate.TRUE:
            in_schedule = Tristate.FALSE

        self._set_state(
            observed_power_w=watts,
            inferred_activity=activity,
            power=power,
            in_schedule=in_schedule,
        )
        if watts is not None:
            self.db.add_power_sample(now, watts)
            if now.minute == 0 and now.second < self.settings.power_sample_seconds:
                self.db.prune_power(now - timedelta(days=7))

    # --- Nightly jobs ---------------------------------------------------------

    async def _run_precool(self, plan: NightPlan) -> None:
        mode = PRECOOL_MODE if self.schedule.mode.is_cooling else self.schedule.mode
        self.events.info(
            "precool",
            f"Pre-cooling in {mode.value} for a {plan.arm_at:%H:%M} arm "
            f"and a {plan.wake_at:%a %H:%M} wake",
        )
        async with self._lock:
            try:
                await self.commands.power_on()
                self._set_state(power=Power.ON, in_schedule=Tristate.FALSE)
                await self.commands.set_mode(mode)
                self._set_state(assumed_mode=mode)
                await self.commands.set_temperature(self.schedule.phase1_temp_c, mode)
                self._set_state(
                    assumed_target_c=self.schedule.phase1_temp_c, last_command_at=self.clock.now()
                )
            except CommandFailed as exc:
                self._fail("precool", exc)

    async def _run_arm(self, plan: NightPlan) -> None:
        self.events.info("schedule_arm", f"Arming for a {plan.wake_at:%a %H:%M} wake")
        async with self._lock:
            try:
                await self.commands.arm_schedule()
                self._set_state(in_schedule=Tristate.TRUE, last_command_at=self.clock.now())

                # Immediately, while the display is still awake from arming. Never
                # as a separate job: the preamble cannot be used during a schedule.
                current = self.state.assumed_mode or PRECOOL_MODE
                if self.schedule.mode.is_cooling and current is not self.schedule.mode:
                    try:
                        await self.commands.set_cooling_speed(self.schedule.mode, current)
                        self._set_state(assumed_mode=self.schedule.mode)
                    except CommandFailed as exc:
                        # A dropped mode press leaves the unit noisier than asked
                        # for but the schedule still runs correctly. Log it rather
                        # than retrying, since a second press advances the cycle
                        # rather than correcting anything.
                        self.events.warning("mode", f"Could not drop the cooling speed: {exc}")
                        self._set_state(assumed_mode=None)
            except CommandFailed as exc:
                self._fail("schedule_arm", exc)

    async def _run_wake_check(self, plan: NightPlan) -> None:
        watts = await self.power.read_watts()
        if watts is None:
            self.events.warning("wake_check", "Plug unreachable, cannot confirm the unit is off")
            self._set_state(power=Power.UNKNOWN, in_schedule=Tristate.UNKNOWN)
            return
        if watts >= self.settings.off_threshold_w:
            self.events.error(
                "wake_check",
                f"The schedule should have finished at {plan.wake_at:%H:%M} but the plug "
                f"still reads {watts:.1f} W. The unit did not switch itself off.",
            )
            self._set_state(in_schedule=Tristate.UNKNOWN)
            return
        self.events.info("wake_check", "Schedule finished and the unit switched itself off")
        self._set_state(power=Power.OFF, in_schedule=Tristate.FALSE, assumed_target_c=None)

    # --- Commands from the app ------------------------------------------------

    async def power_on(self) -> None:
        async with self._lock:
            try:
                await self.commands.power_on()
                self._set_state(
                    power=Power.ON, in_schedule=Tristate.FALSE, last_command_at=self.clock.now()
                )
            except CommandFailed as exc:
                self._fail("power", exc, power=Power.UNKNOWN)

    async def power_off(self) -> None:
        async with self._lock:
            try:
                await self.commands.power_off()
                self._set_state(
                    power=Power.OFF,
                    in_schedule=Tristate.FALSE,
                    assumed_target_c=None,
                    last_command_at=self.clock.now(),
                )
            except CommandFailed as exc:
                self._fail("power", exc, power=Power.UNKNOWN)

    async def set_temperature(self, target_c: int) -> None:
        mode = self.state.assumed_mode or self.schedule.mode
        async with self._lock:
            try:
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

    async def arm_now(self) -> None:
        async with self._lock:
            try:
                await self.commands.arm_schedule()
                self._set_state(in_schedule=Tristate.TRUE, last_command_at=self.clock.now())
            except CommandFailed as exc:
                self._fail("schedule_arm", exc, in_schedule=Tristate.UNKNOWN)
                raise

    async def write_schedule(
        self, on_progress: Callable[[WriteProgress], None] | None = None
    ) -> None:
        """Never called from the tick loop. User action only."""
        async with self._lock:
            try:
                await self.commands.write_schedule(
                    self.schedule.phase_temps, self.schedule.mode, on_progress=on_progress
                )
                self.schedule.last_written_at = self.clock.now()
                self.db.save_schedule(self.schedule)
                self._set_state(
                    assumed_mode=self.schedule.mode,
                    in_schedule=Tristate.TRUE,
                    last_command_at=self.clock.now(),
                )
                self._push_schedule()
            except CommandFailed as exc:
                self._fail("schedule_write", exc, in_schedule=Tristate.UNKNOWN)
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
