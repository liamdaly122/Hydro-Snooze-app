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
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable

from .adapters import build_adapters
from .adapters.fake_transmitter import FakeTransmitter
from .adapters.probes import NAMES, Probes
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
from . import clocksync, pi, watchdog
from .notify import HEARTBEAT_EVERY, Heartbeat, Notifier
from .scheduler import Job, Scheduler
from .sequences import CommandFailed, Commands

log = logging.getLogger(__name__)

#: How long to wait between attempts at a step that did not land.
#:
#: Matched to the blaster's health check, because the usual reason a step fails
#: is that the blaster is off the Wi-Fi, and there is no point trying again
#: before the thing that would tell us it is back has run.
RETRY_AFTER = timedelta(seconds=30)

#: Events to keep. About thirty a night, so this is a couple of months of
#: history, which is far more than anyone reads and still nothing on a card.
EVENTS_KEPT = 2000

#: How long without a completed tick before the scheduler counts as stuck.
#: Generous against a one second loop, and far shorter than a stage boundary.
STUCK_AFTER = timedelta(minutes=3)

#: Below this, an idle reading is the unit not having started rather than having
#: arrived. Power on, mode change and rail-and-count take about thirty seconds of
#: infrared before the compressor is doing anything at all.
MIN_PRECONDITION_SECONDS = 120

#: Past this it is not arriving. The cap on a lead time is two hours, so a run
#: still going after that has answered a different question.
MAX_PRECONDITION_SECONDS = 3 * 60 * 60

#: How far apart the two hose probes have to get before the unit counts as
#: actually working, and how close they have to come back before the bed counts
#: as arrived.
#:
#: Both are measured, not guessed. On the evening the probes went on, a unit
#: heating hard read 34.56C going out and 33.00C coming back, a gap of 1.56C.
#: The same unit an hour later, sat at temperature, read 41.31C and 41.06C: a
#: gap of 0.25C. So a degree is comfortably inside working and comfortably
#: outside settled, and there is a band between the two that neither claims,
#: which is what stops a run flickering between the two answers.
#:
#: Sign is deliberately not part of this. Which probe ended up on which hose was
#: decided with a roll of tape behind a bed, and the size of the gap is the same
#: either way.
WORKING_DELTA_C = 1.0
SETTLED_DELTA_C = 0.4


@dataclass
class PreconditionRun:
    """A pre-conditioning run in flight.

    `worked` is the guard that makes the probe rule safe. A bed that has arrived
    and a bed whose unit has not started yet look identical on the hoses: in both
    cases the water comes back the way it went out. So the probes may not call a
    run finished until they have seen the gap open at least once themselves.
    """

    started: datetime
    mode: Mode
    target_c: int
    #: What the bed read when the presses landed. None if the probes were quiet.
    start_c: float | None = None
    worked: bool = False


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
        self.scheduler = Scheduler(learned_lead=self._learned_lead, bed_now=self._bed_now)
        # Read back what already ran tonight before anything can ask. A restart is
        # a routine event now: systemd brings the service back after a crash and
        # the watchdog brings it back after a stall, so losing this in memory
        # meant a good night reporting itself as a failed one afterwards.
        self.scheduler.fired.done = self.db.fired_marks()
        self.scheduler.fired.store = self.db.set_fired_marks
        self.probes = Probes(
            self.clock,
            settings.probes_host,
            settings.probes_port,
            settings.probes_encryption_key,
        )
        self.notifier = Notifier(self.clock, settings.ntfy_topic, settings.ntfy_server)
        self.heartbeat = Heartbeat(self.clock, settings.heartbeat_url)

        self.schedule: Schedule = self.db.load_schedule()
        self.state = DeviceState()
        self.events.seed(self.db.recent_events(200))

        # None means never asked, which is a different thing from "not answering"
        # and the bar says so rather than showing a colour it has not earned.
        # Which job is being retried, and the earliest another attempt is worth
        # making. Keyed by the job rather than a flag so a different step
        # arriving clears it by itself.
        # A pre-conditioning run in flight: when it started, what it was aiming
        # at, and what the bed read at the time. The probes say when it got
        # there, and the plug says so when the probes cannot.
        self._precondition: PreconditionRun | None = None

        # When the scheduler last completed a tick. The watchdog pings systemd
        # only while this keeps moving, so a loop that is running but stuck stops
        # the pings and gets restarted, which Restart=always would never do.
        self._last_tick_at: datetime | None = None

        self._retrying: str | None = None
        self._retry_after: datetime | None = None

        # Whether the clock has been confirmed against the network yet, and when
        # this started waiting. A Pi has no clock of its own at boot; see
        # clocksync.py. Monotonic, because the whole point is that the other one
        # cannot be trusted.
        self._clock_ok: bool = False
        self._clock_waiting_since: float | None = None

        # Which of the Pi's throttling bits have already been reported. They are
        # sticky until the next boot, so without this one bad power supply would
        # be an hourly push forever.
        self._pi_reported: int = 0

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
        return [plug, blaster, self._probes_health(), self._alerts_health()]

    def _probes_health(self) -> DeviceHealth:
        """The three temperatures, and whether any of them are current.

        Partial failure needs saying rather than rounding to fine or broken. Two
        probes out of three still leaves the flow-to-return difference, or not,
        depending on which two, and that difference is the only thing here that
        cannot be inferred from anything else.

        A probe board that has gone is not a reason to stop: the schedule ran for
        weeks before these existed. It is a reason to know less, and to say so.
        """
        if not self.probes.enabled:
            return DeviceHealth(
                "probes", Health.SIMULATED, "No probes. Temperatures are not measured"
            )

        missing = self.probes.missing()
        readings = {
            "flow": self.probes.flow_c,
            "return": self.probes.return_c,
            "room": self.probes.room_c,
        }
        said = ", ".join(f"{k} {v}C" for k, v in readings.items() if v is not None)

        if not missing:
            moving = self.probes.moving_c
            # The sign is the interesting part, so it is spelled out rather than
            # left as a number to interpret at 3am.
            if moving is None or abs(moving) < 0.3:
                what = "nothing moving"
            elif moving > 0:
                what = f"bed shedding {abs(moving)}C into the water"
            else:
                what = f"water giving {abs(moving)}C to the bed"
            return DeviceHealth("probes", Health.OK, f"{said}. {what}")

        if len(missing) < len(NAMES):
            return DeviceHealth(
                "probes",
                Health.DEGRADED,
                f"{said}. Not hearing from {' or '.join(missing)}",
            )
        return DeviceHealth(
            "probes",
            Health.DOWN,
            f"No readings from the probe board at {self.settings.probes_host}",
        )

    def _alerts_health(self) -> DeviceHealth:
        """Whether anything would actually tell you if this stopped working.

        Not a device, but it belongs beside them: three green dots saying the
        hardware is fine mean very little if nothing is watching at 3am. It is
        also the one row that can be wrong in a way you would never notice,
        because a notifier that is switched off looks exactly like a quiet night.
        """
        pushes = self.notifier.enabled
        beats = self.heartbeat.enabled
        watched = watchdog.interval_seconds() is not None

        on = []
        off = []
        (on if pushes else off).append("push notifications")
        (on if beats else off).append("a heartbeat")
        # Only meaningful under systemd. On a Mac there is nothing to restart it,
        # so its absence is a fact about the machine rather than a misconfiguration.
        if watched:
            on.append("a watchdog")

        if pushes and beats:
            extra = " and a watchdog" if watched else ""
            return DeviceHealth(
                "alerts",
                Health.OK,
                f"Problems reach your phone, and so does this machine going quiet{extra}.",
                self.heartbeat.last_ok_at,
            )
        if pushes or beats:
            return DeviceHealth(
                "alerts",
                Health.DEGRADED,
                f"Only {' and '.join(on)}. Missing {' and '.join(off)}.",
                self.heartbeat.last_ok_at,
            )
        return DeviceHealth(
            "alerts",
            Health.DOWN,
            "Nothing would tell you if this stopped working. "
            "Set both up with ./scripts/notify.py",
        )

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

    async def _watchdog_loop(self, every: float) -> None:
        """Tell systemd we are alive, but only while the scheduler is ticking.

        The distinction is the whole point. A process that exists is not the same
        as one doing its job, and it was the second that failed.
        """
        while True:
            await self.clock.sleep(every)
            last = self._last_tick_at
            stuck = last is not None and (self.clock.now() - last) > STUCK_AFTER
            if stuck:
                log.error("no scheduler tick since %s, letting the watchdog fire", last)
                continue
            watchdog.alive()

    async def _heartbeat_loop(self) -> None:
        """Tell the outside world we are still here, while we still are.

        Same liveness signal the watchdog uses: not "the process exists" but
        "the scheduler is completing ticks". A Pi that is powered but wedged
        should look dead from outside, because for the purposes of a night it is.
        """
        while True:
            last = self._last_tick_at
            ticking = last is not None and (self.clock.now() - last) <= STUCK_AFTER
            if ticking:
                await self.heartbeat.ping()
            else:
                log.error("no scheduler tick since %s, holding the heartbeat back", last)
            await self.clock.sleep(HEARTBEAT_EVERY.total_seconds())

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
        await self.probes.start()
        await self._sample_power()
        self._last_tick_at = self.clock.now()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="scheduler"),
            asyncio.create_task(self._power_loop(), name="power"),
            asyncio.create_task(self._health_loop(), name="health"),
        ]

        every = watchdog.interval_seconds()
        if every is not None:
            self._tasks.append(
                asyncio.create_task(self._watchdog_loop(every), name="watchdog")
            )
            log.info("systemd is watching, pinging every %.0fs", every)
        # Says the unit is up. Type=notify waits for this before calling the
        # service started, and before this everything above has already run.
        watchdog.ready()

        if self.heartbeat.enabled:
            self._tasks.append(
                asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
            )

        if self.notifier.enabled:
            self.events.info("service", "Notifications on. Problems will reach the phone.")
        if self.heartbeat.enabled:
            self.events.info(
                "service",
                "Heartbeat on. If this machine stops saying it is here, you will be told.",
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._unsubscribe_events:
            self._unsubscribe_events()
        await self.probes.close()
        await self.notifier.close()
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
        self.notifier.on_event(event)

    def _push_state(self) -> None:
        from .api.schemas import health_json, state_json

        # Health goes with it rather than on a poll of its own. It changes for
        # the same reasons state does, and a device bar that lags behind the
        # thing it is describing is worse than not having one.
        self._broadcast(
            {"state": state_json(self.state), "health": health_json(self.health())}
        )

    def _push_schedule(self) -> None:
        self._broadcast({"schedule": self.schedule_as_shown()})

    def schedule_as_shown(self) -> dict[str, Any]:
        """The schedule the way the app should see it, measurements included.

        Not `schedule_json(self.schedule)`. That card says when the bed starts
        getting ready and how long it takes, and both depend on where the bed is
        now and on what previous nights took. Serving the bare schedule showed
        the assumptions on screen while the scheduler quietly ran on the real
        numbers, which is the one kind of disagreement this project cannot have.
        """
        from .api.schemas import schedule_json

        return schedule_json(
            self.schedule, bed_c=self.probes.bed_c, learned=self._learned_lead
        )

    # --- Loops ----------------------------------------------------------------

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
                self._last_tick_at = self.clock.now()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("scheduler tick failed")
            await self.clock.sleep(1)

    def _clock_trusted(self) -> bool:
        """Whether it is safe to act on what the clock says.

        A Pi does not know the time until the network tells it, and until then it
        believes it is roughly whenever it last shut down. Scheduling on that
        would run the wrong night, or report a night that has not happened yet as
        missed, and nothing downstream could tell.

        Not a permanent gate. It waits, and if the answer never comes it runs
        anyway and says so, because a bed that never runs is worse than a bed
        that ran on an unconfirmed clock. See clocksync.GIVE_UP_AFTER_S.
        """
        if self._clock_ok:
            return True

        if clocksync.synchronised() is not False:
            # Confirmed, or a machine with a clock of its own, which cannot be
            # asked and does not need to be.
            if self._clock_waiting_since is not None:
                self.events.info(
                    "service", "The clock is set. Scheduling from here."
                )
            self._clock_ok = True
            self._clock_waiting_since = None
            return True

        waited = time.monotonic()
        if self._clock_waiting_since is None:
            self._clock_waiting_since = waited
            self.events.warning(
                "service",
                "This machine does not know the time yet, so nothing is being "
                "scheduled. It has no clock of its own and is waiting for the "
                "network to tell it.",
            )
            return False

        if waited - self._clock_waiting_since < clocksync.GIVE_UP_AFTER_S:
            return False

        self._clock_ok = True
        self.events.error(
            "service",
            "The clock was never confirmed against the network, and waiting "
            f"{clocksync.GIVE_UP_AFTER_S // 60} minutes has not fixed it. "
            "Scheduling anyway, because no night at all is worse, but tonight's "
            "times may be wrong. Check this machine can reach the internet.",
        )
        return True

    async def _tick(self) -> None:
        # Before anything reads the clock. Everything below this line is a
        # decision about what time it is.
        if not self._clock_trusted():
            return

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

        # Anything at all going wrong here counts as the job not landing.
        #
        # This used to let exceptions through to _tick_loop's catch-all, and the
        # cost was severe: neither the success branch nor the failure branch
        # below ran, so the job stayed unmarked AND no backoff was recorded, and
        # due() handed back the same job every second for hours. A blaster that
        # dropped off the Wi-Fi produced a stack trace at 1Hz all night.
        #
        # Catching broadly is deliberate. At this boundary there is no failure
        # that should be treated as anything other than "it did not work, wait
        # and try again", and the alternative is a loop that cannot stop.
        try:
            landed = await self._run_job(job)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s raised", job.key)
            landed = False
            if first_try:
                self.events.error("stage", f"The {job.key} step failed: {exc}")

        if landed:
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
        # Set before anything else can go wrong, so the backoff holds even if the
        # events above throw. This is the line whose absence caused the 1Hz loop.

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
            # Said out loud, not just coloured in. The blaster has always done
            # this and the plug never did, which was the wrong way round: the
            # plug is the only thing here that measures anything, so losing it
            # means nothing can be verified for the rest of the night. A red dot
            # at 2am is a red dot nobody is looking at.
            if self._plug_ok:
                self.events.info("plug", "The plug is answering again")
            else:
                self.events.warning(
                    "plug",
                    f"The plug at {self.settings.shelly_host} is not answering. "
                    "Nothing can be checked against a measurement until it does: "
                    "watts is the only real reading here and the rest is belief.",
                )
            self._push_state()
        # Read on the same beat as the plug so the app gets one coherent picture
        # rather than temperatures and watts from different moments.
        flow, back, room = self.probes.flow_c, self.probes.return_c, self.probes.room_c

        activity = self.settings.thresholds.classify(watts)

        # The plug is the only thing here that is actually observed, so it
        # overrules what we believed about power. Everything else stays a belief.
        power = self.state.power
        if watts is not None:
            power = Power.OFF if watts < self.settings.off_threshold_w else Power.ON

        step = self.scheduler.stage_now(self.schedule, now)
        self._set_state(
            observed_power_w=watts,
            observed_flow_c=flow,
            observed_return_c=back,
            observed_room_c=room,
            inferred_activity=activity,
            power=power,
            current_stage=step.stage if step and power is Power.ON else None,
        )
        if watts is not None:
            self.db.add_power_sample(now, watts)
            # Once an hour, on the hour. Both tables grow every night forever
            # otherwise, on an SD card that is already the likeliest thing in the
            # whole setup to fail. prune_events was written for this and then
            # never called, so events were the one thing growing unbounded.
            if now.minute == 0 and now.second < self.settings.power_sample_seconds:
                self.db.prune_power(now - timedelta(days=7))
                self.db.prune_events(keep=EVENTS_KEPT)
                self._check_the_pi()

    def _check_the_pi(self) -> None:
        """Ask the machine underneath whether it is coping.

        Under-voltage is the commonest reason a Pi behaves as though the software
        is broken, and it never says so out loud: the machine stays up, the
        network stutters, and every symptom points somewhere else. The notes have
        warned about it from the start. Warning is not noticing.

        Only new bits are reported. The sticky ones stay set until the next boot,
        so saying it again every hour would be one problem and a hundred pushes,
        and the notifier would be right to have taught me to ignore it by the
        third night.
        """
        mask = pi.throttled()
        if mask is None:
            return  # Not a Pi. Nothing to ask.
        new = mask & ~self._pi_reported
        if not new:
            return
        self._pi_reported |= mask
        self.events.warning("service", pi.describe(mask))

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
            bed_c=self._bed_now(),
        )
        # A fresh set of marks, so a second rehearsal is not skipped as one that
        # has already fired. This drops the real night's marks too, which is
        # harmless: every job is idempotent, so the worst case is a stage being
        # set to a temperature it is already holding.
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
        """Time how long the bed really takes to get ready, off two sensors.

        The probes lead, because they are about the bed. While the bed is still
        taking heat, the water comes back at a different temperature from the way
        it went out; when that gap closes the exchange has finished. That is the
        bed itself saying it is ready.

        The plug is the fallback, and it answers a slightly different question:
        it knows when the *unit* stopped working. A unit driving towards a
        setpoint draws 170 W cooling or 300 W heating and falls into the idle
        band when it gets there. That was the whole answer until the probes went
        on and it is still the answer whenever they are quiet, which matters:
        the probe board is new, it is on a bedroom Wi-Fi link, and a night must
        not depend on it.

        So the two work together rather than one replacing the other. Neither
        being available is not a failure either. It means nothing can be
        measured, so nothing is recorded, rather than a guess going into the
        table the lead times are learned from.
        """
        run = self._precondition
        if run is None:
            return

        moving = self.probes.moving_c
        activity = None if watts is None else self.settings.thresholds.classify(watts)
        if moving is None and activity is None:
            return

        elapsed = int((now - run.started).total_seconds())

        # Only the probes set this, deliberately. The plug seeing the unit draw
        # is not the same as the probes seeing heat move: right after the presses
        # land the unit is drawing 300 W and the water has not gone anywhere yet,
        # so both hoses still read the room. Letting the plug vouch for that
        # would hand the probes a closed gap they had never seen open, which is
        # the exact thing this flag exists to stop.
        if moving is not None and abs(moving) >= WORKING_DELTA_C:
            run.worked = True

        if moving is not None and run.worked:
            # The probes have seen the gap open, so they are the ones who can say
            # it has closed.
            if abs(moving) <= SETTLED_DELTA_C and elapsed >= MIN_PRECONDITION_SECONDS:
                self._end_precondition(run, elapsed, reached=True, decided_by="probes")
                bed = self.probes.bed_c
                where = f" The bed is at {bed:.1f}C." if bed is not None else ""
                self.events.info(
                    "precool",
                    f"The bed reached {run.target_c}C in {elapsed // 60}m {elapsed % 60}s. "
                    f"Measured on the hoses: the water is coming back within "
                    f"{SETTLED_DELTA_C}C of the way it went out, so the bed has stopped "
                    f"taking heat.{where}",
                )
                return
        elif activity is Activity.IDLE and elapsed >= MIN_PRECONDITION_SECONDS:
            # No probes, or the probes have not seen the unit do anything yet.
            # The second case is a bed that was already at temperature, and the
            # plug is the one that can tell.
            self._end_precondition(run, elapsed, reached=True, decided_by="plug")
            self.events.info(
                "precool",
                f"The bed reached {run.target_c}C in {elapsed // 60}m {elapsed % 60}s. "
                "Measured off the plug, and used to time the next one.",
            )
            return

        # Ran the whole way and never settled. Kept rather than discarded: it
        # means the target was not reachable that night, which is worth more than
        # the timing would have been.
        if elapsed > MAX_PRECONDITION_SECONDS:
            in_charge = "probes" if moving is not None and run.worked else "plug"
            self._end_precondition(run, elapsed, reached=False, decided_by=in_charge)
            bed = self.probes.bed_c
            where = f" The bed got to {bed:.1f}C." if bed is not None else ""
            self.events.warning(
                "precool",
                f"Ran for {elapsed // 60} minutes without settling at {run.target_c}C, so that "
                f"target may not be reachable in this room.{where}",
            )

    def _end_precondition(
        self, run: PreconditionRun, elapsed: int, *, reached: bool, decided_by: str
    ) -> None:
        """Clear the run and write it down, with whatever was measured.

        The temperatures go in as they are, None included. A row with the timing
        and no temperatures is a run decided off the plug, and saying so is worth
        more than filling the columns in with something plausible.
        """
        self._precondition = None
        self.db.record_precondition(
            run.started,
            run.mode.value,
            run.target_c,
            elapsed,
            reached,
            start_c=run.start_c,
            end_c=self.probes.bed_c,
            room_c=self.probes.room_c,
            decided_by=decided_by,
        )

    def _learned_lead(self, mode: Mode, target_c: int) -> int | None:
        return self.db.learned_lead_minutes(mode.value, target_c)

    def _bed_now(self) -> float | None:
        """Where the bed is starting from, for working out the head start.

        None when the probes are quiet, and then everything downstream falls back
        to assuming a room-temperature bed, which is what it did before there was
        anything to measure.
        """
        return self.probes.bed_c

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
                self._precondition = PreconditionRun(
                    started=self.clock.now(),
                    mode=mode,
                    target_c=target,
                    start_c=self.probes.bed_c,
                )
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
        daily schedule: off at 09:00, on at 19:00. Not its auto-off timer, which
        counts from the moment the output switches on and would never fire here,
        because nothing in this app ever switches the plug. It only reads it.
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
                    f"{exc}. The unit will not switch itself off. Check it, and check "
                    "the Shelly's daily schedule is still set: off at 09:00, on at 19:00.",
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
