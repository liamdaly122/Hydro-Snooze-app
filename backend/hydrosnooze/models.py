"""Domain vocabulary for HydroSnooze.

Everything in here is pure: no I/O, no clock reads, no database. Both the scheduler
and the API serialise these types, and `frontend/src/types.ts` mirrors them, so the
UI and the service can never disagree about what a schedule means or what time
anything happens.

Source of truth is the HS1001 manual plus Liam's own testing, recorded in Part 2 of
the build brief. Anything that is an assumption rather than a confirmed fact carries
an ASSUMPTION comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import Enum


class Mode(str, Enum):
    """The four operating modes. Each remembers its own last temperature."""

    QUIET = "quiet"
    STANDARD = "standard"
    TURBO = "turbo"
    WARMING = "warming"

    @property
    def is_cooling(self) -> bool:
        return self is not Mode.WARMING


class Button(str, Enum):
    """The eight remote buttons, one learned infrared code each."""

    POWER = "power"
    SCHEDULE = "schedule"
    TEMP_UP = "temp_up"
    TEMP_DOWN = "temp_down"
    COOL = "cool"
    WARM = "warm"
    TIMER = "timer"
    MUTE = "mute"


class Power(str, Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class Activity(str, Enum):
    """What the plug's power reading implies the unit is doing."""

    OFF = "off"
    IDLE = "idle"
    COOLING = "cooling"
    HEATING = "heating"
    UNKNOWN = "unknown"


class Tristate(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


# --- Temperature ranges -------------------------------------------------------
#
# Not one 15-55 range. It depends on the mode, and getting this wrong is how you
# end up railing the wrong number of presses.

COOLING_RANGE = (15, 35)
WARMING_RANGE = (25, 55)

MODE_RANGE: dict[Mode, tuple[int, int]] = {
    Mode.QUIET: COOLING_RANGE,
    Mode.STANDARD: COOLING_RANGE,
    Mode.TURBO: COOLING_RANGE,
    Mode.WARMING: WARMING_RANGE,
}

#: Presses needed to pin the unit at a mode's minimum from anywhere: the span plus
#: five for luck. This is what makes every temperature set idempotent, and it is
#: also what absorbs the two discarded presses of the wake preamble.
RAIL_MARGIN = 5


def range_for(mode: Mode) -> tuple[int, int]:
    return MODE_RANGE[mode]


def rail_count(mode: Mode) -> int:
    """How many `temp_down` presses pin the unit at this mode's minimum."""
    low, high = range_for(mode)
    return (high - low) + RAIL_MARGIN


def clamp_to_mode(target_c: int, mode: Mode) -> int:
    low, high = range_for(mode)
    return max(low, min(high, target_c))


def is_valid_for_mode(target_c: int, mode: Mode) -> bool:
    low, high = range_for(mode)
    return low <= target_c <= high


# --- The night ----------------------------------------------------------------
#
# The unit has its own Smart Sleep Schedule: three phases of 4h, 4h and 30m,
# fixed, armed with two button presses and then left to run itself. This project
# used to drive it, and it was elegant, but it is a straitjacket. While it runs,
# the unit refuses to change temperature, refuses the timer, and above all refuses
# to switch between cooling and warming. Since a cooler cannot warm a bed, that
# capped the whole thing at "some temperature at or below the bedroom".
#
# So the app drives the night itself instead. It powers the unit on, sets a
# temperature, and comes back at each stage boundary to set another. Outside the
# unit's own schedule everything is unlocked, which means any number of stages,
# any durations, and any mix of heating and cooling in one night.
#
# What that costs is spelled out where it matters: the unit will no longer switch
# itself off, so the app must, and the Shelly's own auto-off timer stops being a
# nicety and becomes the last line of defence.

#: The unit's own Smart Sleep Schedule still exists in the hardware, and the
#: physical remote can still arm it. The app never does, but the simulated unit
#: models it so that using the remote by hand behaves the way the real one would.
UNIT_SCHEDULE_DURATION = timedelta(hours=8, minutes=30)

#: The unit switches itself off after twelve hours with no button press, and this
#: cannot be disabled. Every stage transition resets it, so across a normal night
#: it never fires. It is a backstop of last resort, not something to rely on.
INACTIVITY_CUTOFF = timedelta(hours=12)

#: Rough time each cooling speed takes to pull the bed down to its lowest setting.
#: ASSUMPTION: Part 2 lists these as untested. They are defaults, not facts.
DEFAULT_LEAD_MINUTES: dict[Mode, int] = {
    Mode.QUIET: 60,
    Mode.STANDARD: 45,
    Mode.TURBO: 30,
    Mode.WARMING: 30,
}

#: Pre-conditioning runs Turbo when it is cooling, because it is the fastest way
#: to pull the bed down before bedtime.
PRECOOL_MODE = Mode.TURBO

#: The lowest temperature warming mode can express. Below this the unit cannot
#: heat at all, which is what decides whether a stage cools or warms.
WARMING_FLOOR_C = WARMING_RANGE[0]


def can_preheat_to(target_c: int) -> bool:
    low, high = WARMING_RANGE
    return low <= target_c <= high


def mode_for_target(
    target_c: int,
    cooling_speed: Mode,
    *,
    coming_from_c: int | None = None,
    coming_from_mode: Mode | None = None,
) -> Mode:
    """Whether a stage cools or warms.

    Two thirds of this is forced. Below 25C it has to cool, because warming mode
    cannot express a number that low. Above 35C it has to warm, because cooling
    cannot. Between the two, 25 to 35, the ranges overlap and both modes can be
    set to the number, so the number alone does not decide anything.

    What decides it there is the direction the bed has to move, because only one
    mode can actually move it. Asking for 25C on the way down from 30C in warming
    mode sets the right target and then leaves the unit idle while the bed coasts
    down on its own. That is the same mistake pre-conditioning already guards
    against, made four hours later in the night.

    So: going up warms, going down cools, and standing still changes nothing.

    ASSUMPTION, worth a Shelly reading before it is treated as fact: that warming
    mode does not actively cool. Set warming to 25C with the bed at 30C and watch
    the plug. Around 170 W and it is cooling after all, and none of this matters.

    With nothing to come from, the target alone decides and 25C and above warms:
    nobody asks for a bed at 27C unless they want it warmed to 27C.
    """
    speed = cooling_speed if cooling_speed.is_cooling else Mode.QUIET

    if target_c < WARMING_FLOOR_C:
        return speed
    if target_c > COOLING_RANGE[1]:
        return Mode.WARMING

    if coming_from_c is None:
        return Mode.WARMING
    if coming_from_c > target_c:
        return speed
    if coming_from_c < target_c:
        return Mode.WARMING
    # Same temperature, so nothing has to move and the mode stays as it was. The
    # speed comes from the schedule rather than the old mode, in case it changed.
    if coming_from_mode is not None and coming_from_mode.is_cooling:
        return speed
    return Mode.WARMING


def modes_for(stages: list[SleepStage], cooling_speed: Mode) -> list[Mode]:
    """Every stage's mode, in the order they run.

    A night has to be resolved as a sequence, not a stage at a time: inside the
    overlap a stage's mode depends on the temperature before it. This is the only
    way to ask, and the reason SleepStage has no mode of its own.
    """
    modes: list[Mode] = []
    for index, stage in enumerate(stages):
        previous = stages[index - 1] if index else None
        modes.append(
            mode_for_target(
                stage.temp_c,
                cooling_speed,
                coming_from_c=previous.temp_c if previous else None,
                coming_from_mode=modes[-1] if modes else None,
            )
        )
    return modes


class Stage(str, Enum):
    """The parts of a night, named for what the body is doing.

    Deep sleep is concentrated in the first third of a night and REM lengthens
    through the second half, so the order here is chronological. During REM the
    body regulates its own temperature poorly, which is the usual argument for
    letting the bed run warmer later on.
    """

    DEEP = "deep"
    REM = "rem"
    WAKE = "wake"


STAGE_ORDER: tuple[Stage, ...] = (Stage.DEEP, Stage.REM, Stage.WAKE)

STAGE_LABEL: dict[Stage, str] = {
    Stage.DEEP: "Deep",
    Stage.REM: "REM",
    Stage.WAKE: "Wake",
}


@dataclass
class SleepStage:
    """One part of the night: how long it lasts and how cold or warm it is."""

    stage: Stage
    duration_minutes: int
    temp_c: int


def default_stages() -> list[SleepStage]:
    """A sensible starting night: cold for deep sleep, easing up through REM.

    Four hours deep at 17C, three and a half through REM at 20C, then half an
    hour at 26C to surface on. The last one warms, which is exactly what the
    unit's own scheduler made impossible.
    """
    return [
        SleepStage(Stage.DEEP, 240, 17),
        SleepStage(Stage.REM, 210, 20),
        SleepStage(Stage.WAKE, 30, 26),
    ]


class Precondition(str, Enum):
    """How to get the bed ready before the first stage starts.

    A cooler cannot warm a bed. If the first stage is above whatever the bed is
    resting at, no amount of cooling reaches it, and the app would otherwise
    report a pre-cool that achieved nothing.
    """

    COOL = "cool"
    WARM = "warm"

    def mode_for(self, cooling_speed: Mode) -> Mode:
        if self is Precondition.WARM:
            return Mode.WARMING
        return PRECOOL_MODE if cooling_speed.is_cooling else cooling_speed


@dataclass(frozen=True)
class StageStep:
    """One stage, with the real moment it starts and the mode it needs."""

    stage: Stage
    starts_at: datetime
    ends_at: datetime
    temp_c: int
    mode: Mode

    @property
    def label(self) -> str:
        return STAGE_LABEL[self.stage]


@dataclass(frozen=True)
class NightPlan:
    """One night, as real timestamps.

    Every moment here is a full timestamp on a real date, because bedtime almost
    always falls on the evening *before* the wake morning, and that off-by-one-day
    is exactly the sort of thing that ruins a night.
    """

    precool_at: datetime | None
    bedtime_at: datetime
    wake_at: datetime
    steps: tuple[StageStep, ...]

    @property
    def starts_at(self) -> datetime:
        return self.precool_at or self.bedtime_at

    @property
    def first_temp_c(self) -> int:
        return self.steps[0].temp_c if self.steps else 20


def plan_for_wake(
    wake_on: date,
    wake_time: time,
    stages: list[SleepStage],
    cooling_speed: Mode = Mode.QUIET,
    *,
    precool_enabled: bool = True,
    precool_lead_minutes: int = DEFAULT_LEAD_MINUTES[PRECOOL_MODE],
) -> NightPlan:
    """Work backwards from the morning you want to wake up.

    The stages run in order and finish at the wake time, so bedtime falls out of
    how long they add up to. Wake at 06:30 after 4h deep, 3h30 REM and 30m wake
    means lights out at 22:30, and pre-conditioning starts before that.
    """
    wake_at = datetime.combine(wake_on, wake_time)
    total = timedelta(minutes=sum(s.duration_minutes for s in stages))
    bedtime_at = wake_at - total

    steps: list[StageStep] = []
    cursor = bedtime_at
    for stage, mode in zip(stages, modes_for(stages, cooling_speed), strict=True):
        ends = cursor + timedelta(minutes=stage.duration_minutes)
        steps.append(
            StageStep(
                stage=stage.stage,
                starts_at=cursor,
                ends_at=ends,
                temp_c=stage.temp_c,
                mode=mode,
            )
        )
        cursor = ends

    precool_at = (
        bedtime_at - timedelta(minutes=precool_lead_minutes) if precool_enabled else None
    )
    return NightPlan(
        precool_at=precool_at,
        bedtime_at=bedtime_at,
        wake_at=wake_at,
        steps=tuple(steps),
    )


# --- Schedule -----------------------------------------------------------------


@dataclass
class Schedule:
    """The one saved schedule. v1 has exactly one.

    `days_of_week` is keyed to the **wake morning**, not the arming evening. Waking
    at 06:30 on Tuesday means arming at 22:00 on Monday. That is the intuitive
    reading of "wake me at 06:30 on weekdays", but it is a real ambiguity, so the UI
    never shows a bare time: it always spells out "Arms Mon 22:00, wakes Tue 06:30".

    Monday is 0, matching `date.weekday()`.
    """

    name: str = "Tonight"
    enabled: bool = True
    days_of_week: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    wake_time: time = time(6, 30)
    #: The night, in order. Deep first, because that is when deep sleep happens.
    stages: list[SleepStage] = field(default_factory=default_stages)
    #: Which cooling speed a cooling stage uses. Quiet by default: it is next to
    #: a bed. Warming stages ignore this, the unit has only one warming speed.
    cooling_speed: Mode = Mode.QUIET
    precool_enabled: bool = True
    #: Named `precool_*` because these were the database columns before pre-heating
    #: existed. The user-facing text says "pre-heat" or "pre-cool" to match what is
    #: actually happening.
    precondition: Precondition = Precondition.COOL
    precool_lead_minutes: int = DEFAULT_LEAD_MINUTES[PRECOOL_MODE]
    updated_at: datetime | None = None
    id: int = 1

    @property
    def first_temp_c(self) -> int:
        """The temperature the bed is brought to before the night starts."""
        return self.stages[0].temp_c if self.stages else 20

    @property
    def total_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.stages)

    def stage(self, stage: Stage) -> SleepStage | None:
        return next((s for s in self.stages if s.stage is stage), None)

    @property
    def precondition_mode(self) -> Mode:
        """The mode the unit is put into before the first stage, not during it."""
        return self.precondition.mode_for(self.cooling_speed)

    @property
    def preheat_is_possible(self) -> bool:
        """Warming cannot express a target below 25C, so pre-heating below it
        is not a setting the app can honour."""
        return can_preheat_to(self.first_temp_c)

    def precondition_problem(self) -> str | None:
        """Why this pre-conditioning setting cannot work, or None if it can."""
        if self.precondition is Precondition.WARM and not self.preheat_is_possible:
            return (
                f"Pre-heating cannot reach {self.first_temp_c}C. Warming mode only goes "
                f"down to {WARMING_FLOOR_C}C, so the unit has no way to warm the bed to it."
            )
        return None

    def plan_for(self, wake_on: date) -> NightPlan:
        return plan_for_wake(
            wake_on,
            self.wake_time,
            self.stages,
            self.cooling_speed,
            precool_enabled=self.precool_enabled,
            precool_lead_minutes=self.precool_lead_minutes,
        )

    def next_plan(self, now: datetime) -> NightPlan | None:
        """The next night that has not started yet, or None if the schedule is off.

        Looks ahead eight days so that a schedule running on a single weekday is
        still found once the current week's occurrence has passed.
        """
        if not self.enabled or not self.days_of_week:
            return None
        for offset in range(8):
            wake_on = (now + timedelta(days=offset)).date()
            if wake_on.weekday() not in self.days_of_week:
                continue
            plan = self.plan_for(wake_on)
            if plan.starts_at > now:
                return plan
        return None

    def with_updates(self, at: datetime, **changes: object) -> Schedule:
        return replace(self, updated_at=at, **changes)  # type: ignore[arg-type]


# --- Device state -------------------------------------------------------------


@dataclass
class DeviceState:
    """What the app believes about the unit.

    Almost all of it is belief rather than knowledge. The unit cannot be read over
    infrared, so the only genuinely observed value here is `observed_power_w`, which
    comes from the plug. Everything prefixed `assumed_` was set by us and never
    confirmed. When we lose track, these go to None or UNKNOWN and the app says so
    rather than guessing.
    """

    power: Power = Power.UNKNOWN
    #: Which part of the night is running, if any. The app drives the stages
    #: itself, so unlike everything else prefixed `assumed_` this one is known.
    current_stage: Stage | None = None
    assumed_mode: Mode | None = None
    #: The target we last commanded. Not in the brief's data model, but the Home
    #: screen's "Now" tab has to show and edit something, and this is the only
    #: honest candidate: the last value the app itself sent.
    assumed_target_c: int | None = None
    observed_power_w: float | None = None
    inferred_activity: Activity = Activity.UNKNOWN
    last_command_at: datetime | None = None
    last_error: str | None = None

    @property
    def can_set_temperature(self) -> bool:
        """Only dead when the unit is off, where the power button is the only
        one that responds.

        It used to also be dead during the unit's own sleep schedule. The app no
        longer arms that, so a temperature can be set at any point in the night.
        """
        return self.power is Power.ON


# --- Power thresholds ---------------------------------------------------------


@dataclass(frozen=True)
class PowerThresholds:
    """Calibrate against the real unit at setup.

    Starting points from the manual's 170W cooling and 300W heating.
    """

    off_max_w: float = 5.0
    idle_max_w: float = 60.0
    cooling_max_w: float = 220.0

    def classify(self, watts: float | None) -> Activity:
        if watts is None:
            return Activity.UNKNOWN
        if watts < self.off_max_w:
            return Activity.OFF
        if watts < self.idle_max_w:
            return Activity.IDLE
        if watts < self.cooling_max_w:
            return Activity.COOLING
        return Activity.HEATING


#: The highest temperature the API will accept. Set to the unit's own maximum by
#: Liam's decision, so in practice the only ceiling is the hardware's: a warming
#: stage can be set anywhere in 25 to 55.
#:
#: Lower it in .env to put a software ceiling back. With it here, the Shelly's own
#: auto-off timer is the only thing that limits how long a hot bed stays hot, which
#: is why SETUP.md treats setting that timer as required rather than optional.
DEFAULT_MAX_TEMPERATURE_C = WARMING_RANGE[1]
