"""The nightly routine.

Given a wake time, everything else falls out of it:

    arm_at     = wake_at - 8h30m
    precool_at = arm_at  - lead

At `precool_at` the unit is powered on, forced into Turbo because that is the
fastest way to get the bed cold, and set to the phase 1 temperature. At `arm_at`
the schedule is armed, and then, immediately, in the same job while the display is
still awake from arming, the cooling speed is dropped to whatever was asked for.

That last part matters. Cooling speed can be changed during a running schedule,
but the wake preamble cannot be used there, so the press has to land while the
display is already awake. It is deliberately not a separate scheduled job.

A tick loop rather than a cron library. The whole point of this project is being
able to jump the clock to 21:29 and watch the evening happen, and a loop that asks
"what is due at clock.now()" behaves identically under a real clock and a
simulated one with no special casing. A cron library keeps its own view of wall
time and fights that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from .models import NightPlan, Schedule

Job = Literal["precool", "arm", "wake_check"]

#: How late the service is still willing to arm. Starting up at 2am and arming
#: then would run the schedule until half past ten in the morning, which is worse
#: than not arming at all.
ARM_GRACE = timedelta(minutes=20)

#: How long after the wake time to check the unit actually switched itself off.
WAKE_CHECK_GRACE = timedelta(minutes=15)


@dataclass
class FiredMarks:
    """Which jobs have already run, keyed by the night they belonged to.

    Keyed by wake time rather than by a flag, so a restart does not re-fire a job
    and a second night is never confused with the first.
    """

    precool: datetime | None = None
    arm: datetime | None = None
    wake_check: datetime | None = None

    def mark(self, job: Job, plan: NightPlan) -> None:
        setattr(self, job, plan.wake_at)

    def has_fired(self, job: Job, plan: NightPlan) -> bool:
        return getattr(self, job) == plan.wake_at


@dataclass
class Scheduler:
    """Works out what should be happening. Deliberately has no side effects."""

    fired: FiredMarks = field(default_factory=FiredMarks)

    def plan_in_progress(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """The night we are currently inside, or the next one.

        Looks back a day as well as forward, because the arming evening is almost
        always the day before the wake morning.
        """
        if not schedule.enabled or not schedule.days_of_week:
            return None
        for offset in range(-1, 8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            plan = schedule.plan_for(wake_on)
            if now < plan.wake_at + WAKE_CHECK_GRACE:
                return plan
        return None

    def due(self, schedule: Schedule, now: datetime) -> tuple[Job, NightPlan] | None:
        """The one job that should run right now, if any."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None

        if (
            plan.precool_at is not None
            and plan.precool_at <= now < plan.arm_at
            and not self.fired.has_fired("precool", plan)
        ):
            return "precool", plan

        # Late arming is worse than no arming, so there is a window rather than an
        # open-ended catch-up.
        if (
            plan.arm_at <= now < min(plan.arm_at + ARM_GRACE, plan.wake_at)
            and not self.fired.has_fired("arm", plan)
        ):
            return "arm", plan

        if (
            plan.wake_at <= now < plan.wake_at + WAKE_CHECK_GRACE
            and not self.fired.has_fired("wake_check", plan)
        ):
            return "wake_check", plan

        return None

    def missed_arming(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """A night whose arming window has closed without the job running."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None or self.fired.has_fired("arm", plan):
            return None
        if now >= min(plan.arm_at + ARM_GRACE, plan.wake_at):
            return plan
        return None
