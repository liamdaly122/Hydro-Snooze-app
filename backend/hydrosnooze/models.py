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

from collections.abc import Callable
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
# itself off, so the app must, and the Shelly's daily off/on schedule stops being
# a nicety and becomes the last line of defence.

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

#: Where to assume the bed starts from when nothing can measure it.
#:
#: ASSUMPTION: a UK bedroom overnight. This was the only answer there was until
#: the hose probes went on, and it is still the answer whenever they are quiet.
#: `preconditioning_for` takes the measured temperature when there is one and
#: says which of the two it used, so the app never shows this number dressed up
#: as a reading.
ASSUMED_ROOM_C = 20

#: Getting going costs time whatever the distance: the unit powers on, the water
#: starts moving, and the pad catches up with the water. ASSUMPTION, like every
#: other timing in this file.
PRECONDITION_BASE_MINUTES = 15

#: Closer than this to room temperature and there is nothing worth doing, because
#: the bed is already there.
PRECONDITION_DEADBAND_C = 1

#: However far the bed has to travel, this is the earliest before bedtime the app
#: will ever switch the unit on.
PRECONDITION_MAX_MINUTES = 90

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

    Confirmed on the real unit, not assumed: warming mode does not cool. With the
    bed already warm and warming set to 25C, the plug read 5 W for ninety seconds.
    Cooling draws 166 W and heating draws 306 W, so the unit was doing nothing at
    all, which is exactly what a heater asked to make a bed colder should do.

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


#: How close to the setpoint the bed has to get before cooling can take over.
#:
#: The whole point of this is noise. Warming mode on this unit sounds, in Liam's
#: words, like a geiger counter, and it runs next to someone trying to sleep.
#: Cooling is quiet. So the moment the bed is at the number, the noisy mode has
#: nothing left to do and should stop.
QUIET_ARRIVED_C = 0.5

#: The event kind a mode correction is logged under.
#:
#: Not "mode", which sequences.py has always used for every set_mode there is:
#: every stage boundary, everything pressed by hand, every step of getting the
#: bed ready. The morning report counts these to say how often a night swapped
#: modes to stay quiet, and counting the rest as well had it reporting a number
#: several times too big. Found in a journal where the two sat next to each
#: other and could not be told apart, which was the real cost.
QUIET_KIND = "quiet"

#: And how far it has to fall back before warming is worth the noise again.
#:
#: Wider than the arrival margin on purpose, and the gap between the two is the
#: whole design. A single threshold would have the unit swapping modes every time
#: a probe wobbled half a degree; this way the bed has to genuinely lose ground
#: before anything changes, and the 1.5C between them is a band where whatever is
#: running carries on running.
#:
#: Deliberately generous, because of what it is trading. Two degrees below the
#: setpoint in silence is a better night than exactly the setpoint next to a
#: geiger counter, and that judgement is Liam's rather than mine.
QUIET_FALLEN_C = 2.0


def quieter_mode(
    target_c: int,
    running: Mode,
    bed_c: float | None,
    cooling_speed: Mode,
    *,
    cap_c: int,
) -> Mode | None:
    """The mode this stage should really be in, judged from the bed rather than
    the schedule. None means leave it alone, which is most of the time.

    `mode_for_target` decides at plan time, from the stage before it. That is a
    prediction, made hours early, about a bed with nobody in it. This is the same
    question asked again with the answer in hand, and the two disagree in exactly
    the case worth catching: a stage that steps the temperature up is planned as
    warming, but if there is a body in the bed it is already at the number, and
    warming has nothing to do except make a noise.

    Which mode can actually hold a bed is not symmetric. Warming adds heat and
    cooling removes it, so at the same setpoint with a person in the bed, cooling
    holds by taking away what the body puts in, and it is silent doing it. It
    cannot put heat back. So the moment the bed genuinely drops away from the
    number, only warming can bring it up, and the noise is worth it again.

    Outside the 25 to 35 overlap there is no decision to make: below 25 only
    cooling can express the number and above 35 only warming can.
    """
    if bed_c is None:
        return None
    if not (WARMING_FLOOR_C <= target_c <= COOLING_RANGE[1]):
        return None

    speed = cooling_speed if cooling_speed.is_cooling else Mode.QUIET

    if running is Mode.WARMING:
        # Arrived. Hand it to the quiet mode and let body heat do the rest.
        return speed if bed_c >= target_c - QUIET_ARRIVED_C else None

    # Cooling, and losing. Nothing but warming can put heat back into a bed, and
    # it can only be asked for numbers it can reach.
    if bed_c <= target_c - QUIET_FALLEN_C and target_c <= cap_c:
        return Mode.WARMING
    return None


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


@dataclass(frozen=True)
class Preconditioning:
    """How the bed gets ready before the night starts. Worked out, never chosen.

    Three things decide it: which way the bed has to move from room temperature,
    whether the unit can express the number it has to move to, and how far it has
    to go. `mode` of None means there is nothing to do, and `reason` says why in
    words the app can put on screen.
    """

    mode: Mode | None
    lead_minutes: int
    reason: str

    @property
    def runs(self) -> bool:
        return self.mode is not None


#: Looks up how long this bed has really taken to reach a temperature in a mode.
#: A function rather than a value, because the mode is decided inside and the
#: answer depends on it.
LearnedLead = Callable[[Mode, int], "int | None"]


def preconditioning_for(
    first_temp_c: int,
    cooling_speed: Mode,
    bed_c: float | None = None,
    learned: LearnedLead | None = None,
) -> Preconditioning:
    """Pick the mode and the head start, from the gap the bed has to close.

    This is `mode_for_target` again, with a different place to come from. A stage
    comes from the stage before it; the first stage comes from wherever the bed
    is now. A cooler cannot warm a bed and a heater cannot cool one, so the
    direction picks the mode either way.

    `bed_c` is that starting point, off the hose probes. None means they are not
    reporting, and then this falls back to assuming a room-temperature bed, which
    is what it did for months before there was anything to measure. The two are
    never blurred together: the reason says which one it used, because a guess
    printed as a reading is the one thing this project does not do.

    The awkward case is a first stage above the bed but below 25C. The bed has to
    warm, warming mode cannot express a number that low, and running the cooler at
    a bed that needs heat would be worse than doing nothing. So it does nothing,
    and says so.

    The head start is a fixed cost plus the distance, at whatever rate that mode
    manages across its own range. That rate is still an assumption until a few of
    these have been timed, and `learned` is how the timed answer gets back in.
    """
    start = float(ASSUMED_ROOM_C) if bed_c is None else bed_c
    # Two different claims, and they read differently on purpose.
    said = f"about {ASSUMED_ROOM_C}C" if bed_c is None else f"{start:.1f}C on the hoses"
    gap = first_temp_c - start

    if abs(gap) <= PRECONDITION_DEADBAND_C:
        return Preconditioning(
            None, 0, f"The bed is at {said} already, near enough to {first_temp_c}C to leave alone."
        )

    if gap < 0:
        mode = PRECOOL_MODE
        reason = f"Cooling the bed from {said} down to {first_temp_c}C."
    elif first_temp_c >= WARMING_FLOOR_C:
        mode = Mode.WARMING
        reason = f"Warming the bed from {said} up to {first_temp_c}C."
    else:
        return Preconditioning(
            None,
            0,
            f"The bed has to warm from {said} to {first_temp_c}C, and warming mode only "
            f"goes down to {WARMING_FLOOR_C}C, so the unit has no way to get it there. Body heat "
            f"does that job once you are in it.",
        )

    # Measured beats estimated. The probes watch the gap between the two hoses
    # close, and the plug watches the draw fall, so after a few nights there is a
    # real number for this bed in this room rather than a rate I picked.
    measured = learned(mode, first_temp_c) if learned else None
    if measured is not None:
        return Preconditioning(
            mode,
            min(measured, PRECONDITION_MAX_MINUTES),
            f"{reason} Measured at about {measured} minutes on recent nights.",
        )

    low, high = range_for(mode)
    per_degree = DEFAULT_LEAD_MINUTES[mode] / (high - low)
    lead = PRECONDITION_BASE_MINUTES + abs(gap) * per_degree
    return Preconditioning(
        mode,
        min(round(lead), PRECONDITION_MAX_MINUTES),
        f"{reason} Estimated, until a few of these have been timed.",
    )


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


MINUTES_IN_A_DAY = 24 * 60

#: No stage is allowed to disappear. Fifteen minutes is also the step the app
#: moves a boundary by, so a stage can always be nudged back off its own floor.
MIN_STAGE_MINUTES = 15


def minutes_between(bed_time: time, wake_time: time) -> int:
    """How long the night is.

    Bedtime is nearly always the evening before the wake morning, so this wraps
    midnight: 22:30 to 06:30 is eight hours, not minus sixteen. Setting both to
    the same time means a full day rather than nothing, because a night of zero
    length is not a thing anyone means.
    """
    bed = bed_time.hour * 60 + bed_time.minute
    wake = wake_time.hour * 60 + wake_time.minute
    return (wake - bed) % MINUTES_IN_A_DAY or MINUTES_IN_A_DAY


def fit_stages(stages: list[SleepStage], total_minutes: int) -> list[SleepStage]:
    """Scale the stages to fill the night exactly, keeping their shape.

    Bedtime and the wake time are what get set now, so the night has a length
    before the stages do, and they divide it rather than decide it. Moving
    bedtime an hour later takes that hour off the stages in the proportions they
    already had.

    The parts add up to the whole to the minute, by largest remainder rather than
    rounding each in isolation, and nothing is allowed to fall below the floor.
    """
    if not stages:
        return []
    minutes = divide([s.duration_minutes for s in stages], total_minutes, MIN_STAGE_MINUTES)
    return [
        replace(stage, duration_minutes=m) for stage, m in zip(stages, minutes, strict=True)
    ]


def divide(weights: list[int], total: int, floor: int) -> list[int]:
    """Split `total` in the proportions of `weights`, with a floor under each.

    The parts add up to the whole exactly, by largest remainder rather than
    rounding each in isolation, and anything under the floor is lifted by taking
    from whichever part can most afford it.

    Shared by the real schedule, which divides a night into minutes, and by a
    rehearsal, which divides a few minutes into seconds. Same arithmetic, same
    awkward edges, so the same code: a five minute rehearsal of a night whose
    last stage is a fifteenth of the whole runs into the floor immediately, and
    it should overrun no more than a real schedule does.
    """
    count = len(weights)
    if count == 0:
        return []

    total = max(total, floor * count)
    current = sum(weights)
    raw = [total / count] * count if current <= 0 else [w * total / current for w in weights]
    parts = [int(r) for r in raw]

    # Whatever truncating lost goes back to the parts that lost the most of it.
    by_remainder = sorted(range(count), key=lambda i: raw[i] - parts[i], reverse=True)
    for n in range(total - sum(parts)):
        parts[by_remainder[n % count]] += 1

    # Then lift anything under the floor, taking from whichever part is longest,
    # because that is the one that can most afford it.
    for i in range(count):
        while parts[i] < floor:
            donor = max(range(count), key=lambda j: parts[j])
            if donor == i or parts[donor] <= floor:
                break
            parts[donor] -= 1
            parts[i] += 1

    return parts


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

    preconditioning: Preconditioning
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
    bed_c: float | None = None,
    learned: LearnedLead | None = None,
) -> NightPlan:
    """Work backwards from the morning you want to wake up.

    The stages run in order and finish at the wake time, so bedtime is wherever
    they start. That still lands on the bedtime that was set, because a Schedule
    keeps its stages adding up to exactly the night between the two times.
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

    # Not a setting. Worked out from where the bed starts and where it has to be.
    # With no stages there is no first temperature, so it is handed its own
    # starting point and comes back with nothing to do, which is correct.
    pre = preconditioning_for(
        stages[0].temp_c if stages else ASSUMED_ROOM_C, cooling_speed, bed_c, learned
    )
    precool_at = bedtime_at - timedelta(minutes=pre.lead_minutes) if pre.runs else None
    return NightPlan(
        preconditioning=pre,
        precool_at=precool_at,
        bedtime_at=bedtime_at,
        wake_at=wake_at,
        steps=tuple(steps),
    )


#: A rehearsal stage shorter than this cannot finish its own presses. A stage
#: change is roughly thirty-five presses with real gaps between them, about ten
#: seconds of infrared, and the plug checks either side add more.
MIN_REHEARSAL_STAGE_S = 40

#: How long before the compressed bedtime the pre-conditioning step runs.
#: Measured rather than guessed: pre-conditioning is a power on, a mode change
#: and a rail-and-count, which took 27 seconds end to end when this was watched
#: running. At a 30 second lead the first stage began three seconds after it
#: finished, which works but leaves nothing to see and no room if the unit is
#: slower than the simulation.
REHEARSAL_LEAD_S = 45


def rehearsal_plan(
    stages: list[SleepStage],
    cooling_speed: Mode,
    *,
    now: datetime,
    total_seconds: int,
    bed_c: float | None = None,
) -> NightPlan:
    """A whole night compressed into a few minutes, for testing on real hardware.

    Everything here is real except the clock. Real presses, real gaps between
    them, real plug checks, the same modes chosen the same way, the same stages
    in the same order. Only the durations are scaled down, so a night that takes
    eight hours can be watched happening in five minutes.

    Why not just run the simulated clock faster: `SimClock` divides every sleep
    by its speed, which is exactly right for the simulated unit and exactly wrong
    for a real one. At speed 60 the gaps between presses collapse to 5ms and the
    unit sees a smear rather than thirty-five button presses. So the clock stays
    real and the schedule gets shorter instead.

    Stage proportions are kept, so the night rehearsed is the shape of the night
    that will actually run, not a generic one.
    """
    if not stages:
        raise ValueError("A rehearsal needs at least one stage.")

    lengths = divide(
        [s.duration_minutes for s in stages], total_seconds, MIN_REHEARSAL_STAGE_S
    )

    bedtime_at = now + timedelta(seconds=REHEARSAL_LEAD_S)
    steps: list[StageStep] = []
    cursor = bedtime_at
    for stage, mode, seconds in zip(stages, modes_for(stages, cooling_speed), lengths, strict=True):
        ends = cursor + timedelta(seconds=seconds)
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

    # Pre-conditioning is chosen the same way it is for a real night, so the
    # rehearsal exercises that decision too. Only its head start is shortened.
    pre = preconditioning_for(stages[0].temp_c, cooling_speed, bed_c)
    pre = replace(pre, lead_minutes=max(1, REHEARSAL_LEAD_S // 60))
    return NightPlan(
        preconditioning=pre,
        precool_at=now if pre.runs else None,
        bedtime_at=bedtime_at,
        wake_at=cursor,
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
    #: When the lights go out. With the wake time this is what fixes how long the
    #: night is; the stages divide that up rather than deciding it.
    bed_time: time = time(22, 30)
    #: The night, in order. Deep first, because that is when deep sleep happens.
    #: Their durations always add up to exactly the night, see __post_init__.
    stages: list[SleepStage] = field(default_factory=default_stages)
    #: Which cooling speed a cooling stage uses. Quiet by default: it is next to
    #: a bed. Warming stages ignore this, the unit has only one warming speed.
    cooling_speed: Mode = Mode.QUIET
    updated_at: datetime | None = None
    id: int = 1

    def __post_init__(self) -> None:
        # The one invariant. Bedtime and the wake time say how long the night is,
        # and the stages fill it exactly, so there is no way to hold a schedule
        # whose parts do not add up to its whole. Runs on replace() too, which is
        # how every patch reaches this.
        self.stages = fit_stages(self.stages, self.night_minutes)

    @property
    def night_minutes(self) -> int:
        """Lights out to alarm, wrapping midnight."""
        return minutes_between(self.bed_time, self.wake_time)

    @property
    def first_temp_c(self) -> int:
        """The temperature the bed is brought to before the night starts."""
        return self.stages[0].temp_c if self.stages else 20

    @property
    def total_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.stages)

    def stage(self, stage: Stage) -> SleepStage | None:
        return next((s for s in self.stages if s.stage is stage), None)

    def preconditioning(
        self, bed_c: float | None = None, learned: LearnedLead | None = None
    ) -> Preconditioning:
        """How the bed gets ready tonight. Decided from the schedule, not stored.

        Both arguments are measurements the schedule cannot reach on its own, so
        they are passed in. Left out, this answers with the assumptions, which is
        what a bare schedule with no service behind it can honestly say.
        """
        return preconditioning_for(self.first_temp_c, self.cooling_speed, bed_c, learned)

    def plan_for(
        self,
        wake_on: date,
        learned: LearnedLead | None = None,
        bed_c: float | None = None,
    ) -> NightPlan:
        return plan_for_wake(
            wake_on,
            self.wake_time,
            self.stages,
            self.cooling_speed,
            bed_c=bed_c,
            learned=learned,
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
    infrared, so the only genuinely observed values here are the ones prefixed
    `observed_`: the watts from the plug, and the three temperatures from the
    probe board. Everything prefixed `assumed_` was set by us and never
    confirmed. When we lose track, these go to None or UNKNOWN and the app says so
    rather than guessing.
    """

    power: Power = Power.UNKNOWN
    #: Which part of the night is running, if any. The app drives the stages
    #: itself, so unlike everything else prefixed `assumed_` this one is known.
    current_stage: Stage | None = None
    #: When a compressed rehearsal night finishes, or None if none is running.
    #: Known rather than believed, and it rides along here so it reaches the app
    #: on the same live feed as everything else.
    rehearsal_ends_at: datetime | None = None
    assumed_mode: Mode | None = None
    #: The target we last commanded. Not in the brief's data model, but the Home
    #: screen's "Now" tab has to show and edit something, and this is the only
    #: honest candidate: the last value the app itself sent.
    assumed_target_c: int | None = None
    observed_power_w: float | None = None
    #: Measured, not believed. On the hoses rather than in the bed: flow is the
    #: water the unit is circulating, return is that same water after the bed has
    #: had it, and the difference between them is the heat actually moving.
    #:
    #: None means no probe board, or a reading too old to call current. Never a
    #: guess and never the last one we saw, for the same reason the plug returns
    #: None rather than zero when it cannot be reached.
    observed_flow_c: float | None = None
    observed_return_c: float | None = None
    observed_room_c: float | None = None
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


class Health(str, Enum):
    """How a device is doing, in the only terms this project can honestly use.

    `OK` means the last attempt to reach it worked. `DEGRADED` means it is not
    answering now but was recently, which is what a Wi-Fi wobble looks like and
    is not yet worth waking up for. `DOWN` means it has not answered for long
    enough that the night is at risk. `SIMULATED` is not a colour on a dial: it
    says there is no device here at all, which is different from a healthy one.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


#: How long a device can go unreachable before it stops being a wobble. Chosen
#: against the plug's 30 second sample: five minutes is ten missed reads, which
#: is well past coincidence, and still far short of a stage boundary.
DEGRADED_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class DeviceHealth:
    """One device's health, with enough detail to act on rather than just a dot."""

    name: str
    health: Health
    detail: str
    last_ok_at: datetime | None = None

    @staticmethod
    def judge(
        name: str,
        *,
        now: datetime,
        last_ok_at: datetime | None,
        ok_now: bool | None,
        where: str,
        note: str = "",
    ) -> DeviceHealth:
        """Turn "did the last attempt work, and when did one last work" into a colour."""
        if ok_now is None:
            return DeviceHealth(name, Health.UNKNOWN, f"Not checked yet. {where}".strip())
        if ok_now:
            return DeviceHealth(name, Health.OK, (note or f"Answering at {where}"), last_ok_at)
        if last_ok_at is not None and now - last_ok_at < DEGRADED_AFTER:
            ago = int((now - last_ok_at).total_seconds())
            return DeviceHealth(
                name, Health.DEGRADED, f"No answer for {ago}s. Last reached at {where}", last_ok_at
            )
        return DeviceHealth(
            name, Health.DOWN, f"Not answering at {where}. Check its power and Wi-Fi", last_ok_at
        )


# --- Power thresholds ---------------------------------------------------------


@dataclass(frozen=True)
class PowerThresholds:
    """What the plug's draw says the unit is really doing.

    Measured off a King HS1001 rather than taken from the manual. See the note in
    config.py for the four states and the readings behind them.
    """

    off_max_w: float = 3.0
    idle_max_w: float = 85.0
    cooling_max_w: float = 245.0

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
#: Lower it in .env to put a software ceiling back. With it here, the Shelly's
#: daily off/on schedule is the only thing limiting how long a hot bed stays hot,
#: which is why SETUP.md treats setting it as required rather than optional.
DEFAULT_MAX_TEMPERATURE_C = WARMING_RANGE[1]
