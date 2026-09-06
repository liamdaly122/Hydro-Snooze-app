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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from .models import NightPlan, Schedule, Stage, StageStep

JobKind = Literal["precool", "stage", "power_off"]

#: How late a stage transition is still worth making. Past this the stage is
#: mostly over and setting it would be worse than leaving the bed alone.
STAGE_GRACE = timedelta(minutes=20)

#: The unit no longer switches itself off, so this one is not optional. The
#: window is generous because failing to power off leaves a bed heating all day.
POWER_OFF_GRACE = timedelta(hours=2)


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

    Keyed by wake time rather than a flag, so a restart does not re-fire a job and
    a second night is never confused with the first.
    """

    done: dict[str, datetime] = field(default_factory=dict)

    def mark(self, job: Job) -> None:
        self.done[job.key] = job.plan.wake_at

    def has_fired(self, job: Job) -> bool:
        return self.done.get(job.key) == job.plan.wake_at

    def clear(self) -> None:
        self.done.clear()


@dataclass
class Scheduler:
    """Works out what should be happening. Deliberately has no side effects."""

    fired: FiredMarks = field(default_factory=FiredMarks)

    def plan_in_progress(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """The night we are currently inside, or the next one.

        Looks back a day as well as forward, because bedtime is almost always the
        evening before the wake morning.
        """
        if not schedule.enabled or not schedule.days_of_week or not schedule.stages:
            return None
        for offset in range(-1, 8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            plan = schedule.plan_for(wake_on)
            if now < plan.wake_at + POWER_OFF_GRACE:
                return plan
        return None

    def due(self, schedule: Schedule, now: datetime) -> Job | None:
        """The one job that should run right now, if any."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None

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
        off = Job("power_off", plan)
        if plan.wake_at <= now < plan.wake_at + POWER_OFF_GRACE and not self.fired.has_fired(off):
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
