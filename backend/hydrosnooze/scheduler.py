"""The nightly routine, driven entirely by the app.

The unit's own Smart Sleep Schedule is not used. It locks out temperature changes
and, fatally, refuses to switch between cooling and warming while it runs, which
capped every night at "somewhere at or below the bedroom temperature". Driving it
live costs more infrared and makes the Pi load-bearing all night, but it buys any
number of stages, any durations, and heating and cooling in the same night.

Everything is worked backwards from the wake time. The stages run in order and
finish at it, so bedtime falls out of how long they add up to.

A tick loop rather than a cron library. The whole point of this project is being
able to jump the clock to 21:29 and watch the evening happen, and a loop that asks
"what is due at clock.now()" behaves identically under a real clock and a
simulated one with no special casing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Literal

from .models import LearnedLead, NightPlan, Schedule, Stage, StageStep

log = logging.getLogger(__name__)

JobKind = Literal["wake_blaster", "precool", "stage", "power_off", "report"]

#: How late a stage transition is still worth making. Past this the stage is
#: mostly over and setting it would be worse than leaving the bed alone.
STAGE_GRACE = timedelta(minutes=20)

#: The unit no longer switches itself off, so this one is not optional. The
#: window is generous because failing to power off leaves a bed heating all day.
POWER_OFF_GRACE = timedelta(hours=2)

#: How long after the wake time to send the morning report.
#:
#: After the power off rather than with it, so the report is written about a
#: night that has entirely finished, including whether switching off worked. Far
#: enough back from the alarm that it is read over breakfast rather than in the
#: first confused seconds of being awake.
REPORT_AFTER = timedelta(minutes=20)

#: How long before the bed starts getting ready to restart the blaster.
#:
#: Both times the board wedged it had been powered up for days, answering the
#: network the whole time and emitting nothing. Nothing on this side can see that
#: state, so the answer is not to detect it but to stop entering it: the board
#: that has to work tonight boots half an hour before it is needed.
#:
#: Half an hour rather than five minutes because the point is to be early. If the
#: restart itself goes wrong, there is time to notice and time for the board's own
#: two minute Wi-Fi timeout to have another go, all of it before the first press
#: that actually matters.
WAKE_BLASTER_BEFORE = timedelta(minutes=30)


@dataclass(frozen=True)
class Job:
    kind: JobKind
    plan: NightPlan
    step: StageStep | None = None

    @property
    def key(self) -> str:
        """Identifies this job within its night, so it fires exactly once."""
        if self.kind == "stage" and self.step is not None:
            return f"stage:{self.step.stage.value}"
        return self.kind


@dataclass
class FiredMarks:
    """Which jobs have already run, keyed by the night they belonged to.

    Keyed by wake time rather than a flag, so a second night is never confused
    with the first: a mark left over from yesterday does not match tonight's plan
    and is quietly ignored.

    Written through to storage as well as held in memory, which it was not until
    9 September. That was harmless while a restart was an unusual event. It
    stopped being harmless the day Restart=always and the watchdog made restarts
    routine and the notifier started pushing to a phone: the marks went with the
    process, the next tick decided every stage that had already run had been
    missed, and a night that went perfectly rang two alarms at 3am. A false alarm
    at 3am is worse than no alarm, because it is how you learn to ignore the real
    one.

    The store is a plain callback rather than a database handle, so the scheduler
    keeps knowing nothing about where any of this is kept. Left unset, as it is in
    every test and in the simulator, this behaves exactly as it always did.
    """

    done: dict[str, datetime] = field(default_factory=dict)
    #: Called with the whole set whenever it changes.
    store: Callable[[dict[str, datetime]], None] | None = None

    def mark(self, job: Job) -> None:
        self.done[job.key] = job.plan.wake_at
        self._save()

    def has_fired(self, job: Job) -> bool:
        return self.done.get(job.key) == job.plan.wake_at

    def keys_for(self, plan: NightPlan) -> set[str]:
        """Which jobs have already run for this particular night.

        The marks outlive a night by design, so asking "what ran" without naming
        the night would answer with yesterday's as well.
        """
        return {key for key, at in self.done.items() if at == plan.wake_at}

    def clear(self) -> None:
        self.done.clear()
        self._save()

    def _save(self) -> None:
        """Never allowed to take the night down with it.

        A mark is written after its job has already run, so a failure here means
        the next restart repeats an idempotent sequence. Raising instead would
        abandon the tick that was reporting a success.
        """
        if self.store is None:
            return
        try:
            self.store(dict(self.done))
        except Exception:  # noqa: BLE001
            log.exception("could not save the fired marks")


@dataclass
class Scheduler:
    """Works out what should be happening. Deliberately has no side effects."""

    fired: FiredMarks = field(default_factory=FiredMarks)
    #: A compressed night standing in for the real one, set while a rehearsal is
    #: running. It is a whole NightPlan rather than a special case, so everything
    #: below it runs unchanged: the same due(), the same grace windows, the same
    #: fired marks. A rehearsal is not a different code path, it is a short night.
    rehearsal: NightPlan | None = None
    #: How long this bed has really taken, when there is enough history to say.
    #: Set by the service; the scheduler itself stays free of side effects.
    learned_lead: LearnedLead | None = None
    #: What the hose probes read right now, or None when they are not reporting.
    #:
    #: A callable rather than a number, because the plan is worked out fresh on
    #: every tick and the whole point of this one is that it moves. A cold room
    #: means a longer job, and until this existed the head start was worked out
    #: from an assumed 20C bedroom whatever the bed was actually doing.
    bed_now: Callable[[], float | None] | None = None

    def plan_in_progress(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """The night we are currently inside, or the next one.

        Looks back a day as well as forward, because bedtime is almost always the
        evening before the wake morning.
        """
        # A rehearsal suppresses the real night while it runs. Two nights at once
        # would fight over the unit, and the real one is hours away in any case.
        if self.rehearsal is not None:
            return self.rehearsal
        if not schedule.enabled or not schedule.days_of_week or not schedule.stages:
            return None
        # Read once, so every night considered in the loop below is worked out
        # from the same reading rather than from whatever arrived mid-loop.
        bed = self.bed_now() if self.bed_now else None
        for offset in range(-1, 8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            plan = schedule.plan_for(wake_on, self.learned_lead, bed)
            if now < plan.wake_at + POWER_OFF_GRACE:
                return plan
        return None

    def last_finished(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """The most recent night that is over, for the morning report to describe.

        plan_in_progress answers "which night are we inside", and inside a night
        is exactly when there is nothing to report yet. This walks back instead,
        past a night still running, to the last wake time that has been and gone.

        A fortnight of lookback rather than a couple of days, so a schedule set to
        weekdays only still has something to show on a Sunday.
        """
        if not schedule.days_of_week or not schedule.stages:
            return None
        for back in range(0, 15):
            wake_on = (now - timedelta(days=back)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            plan = schedule.plan_for(wake_on, self.learned_lead)
            if plan.wake_at < now:
                return plan
        return None

    def due(self, schedule: Schedule, now: datetime) -> Job | None:
        """The one job that should run right now, if any."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None

        # Before anything else tonight, and before the first press that matters.
        # A board fresh from a reboot is in a known state; one that has been up
        # for days is the state both wedges were found in.
        #
        # Never in front of a rehearsal. A rehearsal compresses the whole night
        # into a few minutes, so its first stage is seconds away rather than half
        # an hour, and rebooting the board would take it off the network exactly
        # as the first press went out. A rehearsal exists to answer "does every
        # stage boundary really land", and a precaution that sabotages the test
        # of the thing it protects is worse than no precaution.
        starts = plan.precool_at or plan.bedtime_at
        wake_at = starts - WAKE_BLASTER_BEFORE
        if (
            self.rehearsal is None
            and wake_at <= now < starts
            and not self.fired.has_fired(wake := Job("wake_blaster", plan))
        ):
            return wake

        if (
            plan.precool_at is not None
            and plan.precool_at <= now < plan.bedtime_at
            and not self.fired.has_fired(precool := Job("precool", plan))
        ):
            return precool

        # Latest first, so a service that starts up mid-night goes straight to the
        # stage that should be running rather than walking through the earlier
        # ones and leaving the bed at the wrong temperature.
        for step in reversed(plan.steps):
            job = Job("stage", plan, step)
            if step.starts_at <= now < min(step.ends_at, step.starts_at + STAGE_GRACE):
                if not self.fired.has_fired(job):
                    return job
                break

        # Not optional any more. Without the unit's own schedule, nothing else
        # turns it off.
        closes = plan.wake_at + POWER_OFF_GRACE
        off = Job("power_off", plan)
        off_due = plan.wake_at <= now < closes and not self.fired.has_fired(off)

        # After the power off, so the report describes a night that is completely
        # over, including whether switching off worked. A fired mark like
        # everything else here, so it is sent once and a restart does not send it
        # again.
        report = Job("report", plan)
        report_due = (
            plan.wake_at + REPORT_AFTER <= now < closes and not self.fired.has_fired(report)
        )

        # Until the report is actually due, switching off comes first. After
        # that, the report goes ahead of it.
        #
        # It used to wait unconditionally, and the cost showed up on 11 September:
        # a power off in trouble kept being handed back here, so the report was
        # held behind it and did not arrive. That is exactly backwards. The
        # morning the report is most worth reading is the morning something went
        # wrong, and it can say so: the power off keeps its place in the queue and
        # carries on straight afterwards.
        if report_due:
            return report
        if off_due:
            return off

        return None

    def missed(self, schedule: Schedule, now: datetime) -> list[Job]:
        """Jobs whose window has closed without them running.

        Worth saying out loud. A missed stage means the bed spent that stretch of
        the night at the wrong temperature, and a missed power off means it is
        still running.
        """
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return []
        out: list[Job] = []
        for step in plan.steps:
            job = Job("stage", plan, step)
            closed = min(step.ends_at, step.starts_at + STAGE_GRACE)
            if now >= closed and not self.fired.has_fired(job):
                out.append(job)
        return out

    def stage_now(self, schedule: Schedule, now: datetime) -> StageStep | None:
        """Which stage the night is currently in, for the app to display."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None
        return next((s for s in plan.steps if s.starts_at <= now < s.ends_at), None)


_ = Stage
