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
from datetime import date, datetime, timedelta
from typing import Callable, Literal

from .models import (
    Holiday,
    LearnedLead,
    NightPlan,
    Schedule,
    Stage,
    StageStep,
    Tonight,
    Underway,
)

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

    Keyed by night rather than a flag, so a second night is never confused with
    the first. Until 22 September each job kept one mark, stamped with an exact
    wake time, so tonight's overwrote last night's and a changed wake time
    orphaned everything already done. See `night` for what a night is now.

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

    #: Prefix on the key of a job that was given up on rather than run.
    #:
    #: A missed stage is marked like any other, to stop the tick reporting it
    #: every second for the rest of the night. That made it indistinguishable
    #: from a stage that worked: the morning report asks this set what ran, and
    #: a night with a missed Deep stage reported three of three landed. The one
    #: morning that report is worth reading is the morning something went wrong,
    #: and it said the night was perfect.
    GAVE_UP = "missed:"

    #: Prefix on a stage that was not run because the night was being wound up:
    #: automation switched off, or tonight skipped, part way through. Different
    #: from missed and it has to stay different. Missed means something failed
    #: and is an error at 3am; cancelled means somebody asked for exactly this.
    CANCELLED = "cancelled:"

    #: Between the night and the job in a key.
    SEP = "|"
    REHEARSAL = "rehearsal@"

    #: How many nights to remember. Two, because the morning report and
    #: Autopilot describe last night while tonight is already marking, and one
    #: used to be the whole problem: tonight's first stage overwrote last
    #: night's, and a perfect night read as missed from 22:30 the next evening.
    #: Not more, because this table lives on an SD card.
    NIGHTS_KEPT = 2
    REHEARSALS_KEPT = 1

    @classmethod
    def night(cls, plan: NightPlan) -> str:
        """What a night is called, for the purpose of remembering it.

        The wake date, not the wake time. Sleep in moves the wake time by a
        quarter of an hour and the night is the same night; keyed on the exact
        time, every stage that had already run stopped matching, and at 3am
        Drift and Deep were reported missed and pushed to the phone.

        A rehearsal is named by its exact wake time instead, because two of them
        can happen on one date and the second must not find the first's marks.
        Which is what clearing every mark used to be for, and clearing every
        mark also cleared the real night's.
        """
        if plan.rehearsal:
            return f"{cls.REHEARSAL}{plan.wake_at.isoformat()}"
        return plan.wake_at.date().isoformat()

    def _key(self, job: Job, prefix: str = "") -> str:
        return f"{self.night(job.plan)}{self.SEP}{prefix}{job.key}"

    def load(self, marks: dict[str, datetime]) -> None:
        """Read marks back from storage, including any written the old way.

        Before 22 September a key was just the job, stamped with its wake time.
        Those are the night of their wake date, which is what they meant.
        """
        migrated = False
        self.done = {}
        for key, at in marks.items():
            if self.SEP not in key:
                key = f"{at.date().isoformat()}{self.SEP}{key}"
                migrated = True
            self.done[key] = at
        if migrated:
            self._prune()
            self._save()

    def mark(self, job: Job, *, ran: bool = True, cancelled: bool = False) -> None:
        prefix = self.CANCELLED if cancelled else ("" if ran else self.GAVE_UP)
        self.done[self._key(job, prefix)] = job.plan.wake_at
        self._prune()
        self._save()

    def gave_up_on(self, job: Job) -> bool:
        return self._key(job, self.GAVE_UP) in self.done

    def has_fired(self, job: Job) -> bool:
        """Whether this job is finished with, one way or another.

        All three marks count. due() must not offer a stage again once it has
        been given up on or cancelled, or the tick would keep retrying a stage
        whose window closed hours ago. What ran, what was abandoned and what was
        called off are told apart by keys_for and cancelled_for.
        """
        return any(
            self._key(job, prefix) in self.done
            for prefix in ("", self.GAVE_UP, self.CANCELLED)
        )

    def keys_for(self, plan: NightPlan) -> set[str]:
        """Which jobs actually ran for this particular night."""
        return self._outcomes(plan, "")

    def cancelled_for(self, plan: NightPlan) -> set[str]:
        """Which jobs were called off for this night rather than run or missed."""
        return self._outcomes(plan, self.CANCELLED)

    def began(self, plan: NightPlan) -> bool:
        """Whether this night got as far as driving the unit.

        Getting the bed ready or a stage, however it ended. One that ran or was
        given up on means the night was under way; one called off means it was
        already being wound up, and has to carry on being wound up on the next
        tick rather than vanish half way. The blaster restart does not count,
        because it happens before the unit is touched at all.
        """
        head = self.night(plan) + self.SEP
        for key in self.done:
            if not key.startswith(head):
                continue
            job = key[len(head):]
            for prefix in (self.GAVE_UP, self.CANCELLED):
                job = job.removeprefix(prefix)
            if job == "precool" or job.startswith("stage:"):
                return True
        return False

    def _outcomes(self, plan: NightPlan, prefix: str) -> set[str]:
        head = self.night(plan) + self.SEP
        out: set[str] = set()
        for key in self.done:
            if not key.startswith(head):
                continue
            job = key[len(head):]
            if prefix:
                if job.startswith(prefix):
                    out.add(job[len(prefix):])
            elif not job.startswith((self.GAVE_UP, self.CANCELLED)):
                out.add(job)
        return out

    def clear(self) -> None:
        """Forget everything. For the simulator, which jumps between nights.

        Never for a rehearsal any more. A rehearsal is its own night now, and
        clearing on its account took the real night's marks with it.
        """
        self.done.clear()
        self._save()

    def _prune(self) -> None:
        """Keep the most recent nights and the most recent rehearsal, no more."""
        latest: dict[str, datetime] = {}
        for key, at in self.done.items():
            night = key.split(self.SEP, 1)[0]
            if night not in latest or at > latest[night]:
                latest[night] = at
        real = sorted(
            (n for n in latest if not n.startswith(self.REHEARSAL)),
            key=latest.__getitem__,
            reverse=True,
        )
        tests = sorted(
            (n for n in latest if n.startswith(self.REHEARSAL)),
            key=latest.__getitem__,
            reverse=True,
        )
        keep = set(real[: self.NIGHTS_KEPT]) | set(tests[: self.REHEARSALS_KEPT])
        if len(keep) == len(latest):
            return
        self.done = {k: v for k, v in self.done.items() if k.split(self.SEP, 1)[0] in keep}

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
    #: The exception to the routine, for one night. None most of the time.
    tonight: Tonight | None = None
    #: Away from home. Every night inside it is treated the way a skipped night
    #: is, so nothing below has to know the difference. None most of the time.
    holiday: Holiday | None = None
    #: What the hose probes read right now, or None when they are not reporting.
    #:
    #: A callable rather than a number, because the plan is worked out fresh on
    #: every tick and the whole point of this one is that it moves. A cold room
    #: means a longer job, and until this existed the head start was worked out
    #: from an assumed 20C bedroom whatever the bed was actually doing.
    bed_now: Callable[[], float | None] | None = None
    #: What tonight's getting ready was, once it has started. Set by the service
    #: when pre-conditioning begins, and read back after a restart.
    underway: Underway | None = None

    def plan_in_progress(self, schedule: Schedule, now: datetime) -> NightPlan | None:
        """The night we are currently inside, or the next one.

        Looks back a day as well as forward, because bedtime is almost always the
        evening before the wake morning.
        """
        # A rehearsal suppresses the real night while it runs. Two nights at once
        # would fight over the unit, and the real one is hours away in any case.
        if self.rehearsal is not None:
            return self.rehearsal
        if not schedule.days_of_week or not schedule.stages:
            return None
        # Read once, so every night considered in the loop below is worked out
        # from the same reading rather than from whatever arrived mid-loop.
        bed = self.bed_now() if self.bed_now else None
        for offset in range(-1, 8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            skipped = self.running(schedule, wake_on) is None
            # A skipped night is still planned out, because a night that has
            # already started has a unit running in it and a switch-off to
            # perform. Over tonight either way, so skipping does not also undo a
            # lie-in: the stages are what skip cancels, not the deadline. `due`
            # below serves only the finishing jobs once `only_finishing` is true.
            plan = self._as_decided(
                self.shape(schedule, wake_on).plan_for(wake_on, self.learned_lead, bed)
            )
            if now < plan.wake_at + POWER_OFF_GRACE:
                # Skipping is about a night that has not begun. Past its start
                # it means the same as switching automation off mid-night: stop
                # the stages, keep the promise. It used to drop the plan
                # outright, and because the loop then carried on to *tomorrow*,
                # the app was left holding a perfectly good plan for the wrong
                # night while tonight's bed ran until the Shelly caught it.
                if skipped and now < plan.starts_at:
                    continue
                # A night away that never began is not a night at all, however
                # late it gets. The clock alone cannot say that: past its start
                # time, a night skipped a week in advance looks exactly like one
                # skipped at 2am, and winding it up meant a line for every stage,
                # a switch-off, and a morning report about a bed nobody was in,
                # for every night of the holiday. What ran for it can say it.
                if self.away(wake_on) and not self.fired.began(plan):
                    continue
                # Switching automation off stops the next night. It does not
                # abandon one already under way.
                #
                # It used to. The toggle on the Alarm card dropped the whole
                # plan the moment it was flipped, and with it the power off
                # nothing else in the house performs, so flicking it at 2am
                # left the bed running until the Shelly's own schedule caught
                # it seven hours later. A setting is about future nights; what
                # to do with the unit that is running right now is not a
                # setting, and `due` below serves only the finishing jobs once
                # this is off.
                if not schedule.enabled and now < plan.starts_at:
                    return None
                return plan
        return None

    def night_date(self, schedule: Schedule, now: datetime) -> date | None:
        """Which night we are inside, or heading for, as a calendar date.

        Worked out from the **saved** schedule rather than from tonight's version
        of it, which is what stops this and Tonight chasing each other: you need
        the date to look tonight up, so the date cannot depend on what tonight
        says. Sleeping in moves the hour and almost never the date, and skipping a
        night still needs the date to know which night is being skipped.
        """
        if not schedule.days_of_week or not schedule.stages:
            return None
        for offset in range(-1, 8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in schedule.days_of_week:
                continue
            # The date still comes from the saved schedule, so this cannot chase
            # tonight round in a circle. Only the window does: during a lie-in
            # the saved alarm is not the alarm tonight will use, and closing at
            # the old one moved the lookup on to the next night, found nothing
            # stored for it, and took the shift with it on the next restart.
            closes = self.shape(schedule, wake_on).plan_for(wake_on, self.learned_lead).wake_at
            if now < closes + POWER_OFF_GRACE:
                return wake_on
        return None

    def shape(self, schedule: Schedule, wake_on: date) -> Schedule:
        """The night's shape, whether or not it is being skipped.

        `running` answers "what should this night do", and for a skipped night
        that is nothing. This answers "how long is it and when does it end",
        which a skipped night still has: the unit may be on, and the time it has
        to be off by is the one that was asked for, not the one on the routine.
        """
        tonight = self.tonight
        if tonight is None or not tonight.applies_on(wake_on):
            return schedule
        return tonight.over(schedule)

    def running(self, schedule: Schedule, wake_on: date) -> Schedule | None:
        """The schedule as this particular night is being run.

        The saved one most nights, and the saved one with tonight's exceptions
        laid over it when there are any. None when the night is being skipped,
        which leaves the weekly routine exactly as it was: skipping a Tuesday is
        not the same as deciding you no longer sleep on Tuesdays.

        A night away on holiday is None too, for the same reason and ahead of
        anything tonight says. Tonight's temperatures are for somebody in the bed.
        """
        if self.away(wake_on):
            return None
        tonight = self.tonight
        if tonight is None or not tonight.applies_on(wake_on):
            return schedule
        if tonight.skip:
            return None
        return tonight.over(schedule)

    def away(self, wake_on: date) -> bool:
        """Whether this night is one of the nights away on holiday."""
        return self.holiday is not None and self.holiday.away_on(wake_on)

    def only_finishing(self, schedule: Schedule, now: datetime) -> bool:
        """Whether tonight is being wound up rather than run.

        True once automation has been switched off part way through a night. The
        stage changes still to come are cancelled, because that is what the
        toggle means; switching the unit off is not cancelled, because that is a
        promise rather than a preference.

        Skip says the same thing about one night, and it has to be asked with a
        date in hand. Taught about skip and not about the calendar, a Pi still
        holding last night's row cancelled every stage of a night that was going
        to run perfectly well, which is a worse failure than the one that change
        was made to fix: that lost a switch-off, this loses the whole night.

        A holiday says it about a run of nights, and is asked the same way. It
        only changes anything for a night that had already started when the
        holiday was set. One that had not is never planned at all, so there is
        nothing of it to wind up.
        """
        if self.rehearsal is not None:
            return False
        if not schedule.enabled:
            return True
        wake_on = self.night_date(schedule, now)
        if wake_on is None:
            return False
        if self.away(wake_on):
            return True
        tonight = self.tonight
        return tonight is not None and tonight.applies_on(wake_on) and tonight.skip

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
            running = self.running(schedule, wake_on)
            if running is None:
                continue  # a skipped night has nothing to report
            plan = self._as_decided(running.plan_for(wake_on, self.learned_lead))
            if plan.wake_at < now:
                return plan
        return None

    def _as_decided(self, plan: NightPlan) -> NightPlan:
        """A night whose getting ready has begun keeps what was decided for it.

        See models.Underway. The stages, the alarm and everything else still
        come from the plan as it is now; only how the bed was got ready is held.
        """
        return self.underway.over(plan) if self.underway is not None else plan

    def due(self, schedule: Schedule, now: datetime) -> Job | None:
        """The one job that should run right now, if any."""
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None

        if self.only_finishing(schedule, now):
            return self._winding_up(plan, now)

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

        return self._winding_up(plan, now)

    def _winding_up(self, plan: NightPlan, now: datetime) -> Job | None:
        """Switching the unit off, and then saying how the night went.

        Its own method because it is also the whole of what a night does once
        automation has been switched off mid-way. Everything above this is about
        driving a night; this is about finishing one, and finishing one is not
        optional.
        """
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

        Not while the night is being wound up. due() stops offering stages then,
        on purpose, and this used to report every one of them as missed as its
        window closed: switch automation off at 2am and REM rang as an error at
        3am. Those are `cancelled` below.
        """
        if self.only_finishing(schedule, now):
            return []
        return self._closed_unfired(schedule, now)

    def cancelled(self, schedule: Schedule, now: datetime) -> list[Job]:
        """Stages whose window closed while the night was being wound up."""
        if not self.only_finishing(schedule, now):
            return []
        return self._closed_unfired(schedule, now)

    def _closed_unfired(self, schedule: Schedule, now: datetime) -> list[Job]:
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
        """Which stage the night is being run in, or None if none is.

        None once the night is being wound up, and that is the whole of how the
        sampling beat is kept from driving it. due() stopped offering stages
        when automation was switched off, but the beat asked this, got REM back,
        and set REM's temperature anyway. Everything that acts on the plan
        between boundaries asks here first, so saying there is no stage running
        stops all of it, which is also the honest answer for the app to show.
        """
        if self.only_finishing(schedule, now):
            return None
        plan = self.plan_in_progress(schedule, now)
        if plan is None:
            return None
        return next((s for s in plan.steps if s.starts_at <= now < s.ends_at), None)


_ = Stage
