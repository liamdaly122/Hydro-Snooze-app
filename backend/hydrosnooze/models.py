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


# --- The Smart Sleep Schedule -------------------------------------------------
#
# Three phases, 4h then 4h then 30m. Fixed, cannot be changed. So the schedule
# always runs 8h30m from the moment it is armed and then the unit switches off,
# which is the whole trick: "wake me at 06:30" is "arm at 22:00", and the unit
# never needs a clock of its own.

PHASE_DURATIONS = (timedelta(hours=4), timedelta(hours=4), timedelta(minutes=30))
SCHEDULE_DURATION = sum(PHASE_DURATIONS, timedelta())  # 8h30m

#: Rough time each cooling speed takes to pull the bed down to its lowest setting.
#: ASSUMPTION: Part 2 lists these as untested. They are defaults, not facts.
#: Pre-cooling always runs Turbo because it is fastest, so 30 is the default that
#: actually gets used; the rest are here for when you want to pre-cool in the
#: night mode instead.
DEFAULT_LEAD_MINUTES: dict[Mode, int] = {
    Mode.QUIET: 60,
    Mode.STANDARD: 45,
    Mode.TURBO: 30,
    Mode.WARMING: 30,
}

#: Pre-cooling is always done in Turbo. Part 3: "always turbo for pre-cooling, it
#: is fastest". The night mode is applied afterwards, once the schedule is armed.
PRECOOL_MODE = Mode.TURBO


@dataclass(frozen=True)
class NightPlan:
    """The three moments of one night, derived from a wake time.

    Every one of these is a real timestamp on a real date, because the arm and
    pre-cool steps almost always fall on the evening *before* the wake morning and
    that is exactly the sort of off-by-one-day that ruins a night's sleep.
    """

    precool_at: datetime | None
    arm_at: datetime
    wake_at: datetime

    @property
    def starts_at(self) -> datetime:
        return self.precool_at or self.arm_at


def plan_for_wake(
    wake_on: date,
    wake_time: time,
    *,
    precool_enabled: bool = True,
    precool_lead_minutes: int = DEFAULT_LEAD_MINUTES[PRECOOL_MODE],
) -> NightPlan:
    """Work backwards from the morning you want to wake up.

        arm_at     = wake_at - 8h30m
        precool_at = arm_at  - lead

    Wake 06:30 gives arm 22:00 the previous evening, gives pre-cool 21:30.
    """
    wake_at = datetime.combine(wake_on, wake_time)
    arm_at = wake_at - SCHEDULE_DURATION
    precool_at = arm_at - timedelta(minutes=precool_lead_minutes) if precool_enabled else None
    return NightPlan(precool_at=precool_at, arm_at=arm_at, wake_at=wake_at)


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
    phase1_temp_c: int = 19
    phase2_temp_c: int = 17
    phase3_temp_c: int = 21
    mode: Mode = Mode.QUIET
    precool_enabled: bool = True
    precool_lead_minutes: int = DEFAULT_LEAD_MINUTES[PRECOOL_MODE]
    #: When these temperatures were last actually pushed to the unit over infrared.
    #: The app cannot read the unit back, so this is the only handle it has on
    #: whether what is saved here matches what the unit is holding.
    last_written_at: datetime | None = None
    updated_at: datetime | None = None
    id: int = 1

    @property
    def phase_temps(self) -> tuple[int, int, int]:
        return (self.phase1_temp_c, self.phase2_temp_c, self.phase3_temp_c)

    @property
    def needs_write(self) -> bool:
        """True when the saved temperatures have not been pushed to the unit yet.

        This is what raises the Save button in the app. It is deliberately derived
        from timestamps rather than tracked as a flag in the UI, so a reload or a
        second phone cannot lose track of it.
        """
        if self.last_written_at is None:
            return True
        if self.updated_at is None:
            return False
        return self.updated_at > self.last_written_at

    def plan_for(self, wake_on: date) -> NightPlan:
        return plan_for_wake(
            wake_on,
            self.wake_time,
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
    in_schedule: Tristate = Tristate.UNKNOWN
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
        """Temperature adjustment is dead while the sleep schedule is running.

        Also dead when the unit is off, where only the power button responds.
        """
        return self.power is Power.ON and self.in_schedule is Tristate.FALSE


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


#: Hard safety cap. This is a heater capable of 55C under a bed, so the API rejects
#: anything above this regardless of what the mode's range allows.
DEFAULT_MAX_TEMPERATURE_C = 30
