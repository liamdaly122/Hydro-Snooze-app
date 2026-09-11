"""Autopilot: last night as numbers, for the screen rather than for the phone.

`report.py` turns a night into four lines worth reading over breakfast. This
turns the same night into the structure the app draws: how many times the system
changed something, when each one happened, how far off its setpoint the bed was
at that moment, and the cards at the top.

**The categories are named for what this system can actually see.** The design
this is modelled on runs on a bed that measures sleep stages, and it labels its
adjustments "sleep stage response". HydroSnooze measures water going out, water
coming back, the room and watts. So nothing here is called a sleep stage
response, because nothing here can see a sleep stage. What it can see is the
three real reasons the app ever changes anything:

    phase      a stage boundary. The plan, arriving on time
    ambient    getting the bed ready before anyone is in it
    response   a correction made mid-stage, because the bed drifted

**The boosts are invented and say so.** Liam asked for them, and asked for them
to be fun. They are still the one thing in this project that is not measured, so
two rules keep them honest: they are derived from how tightly the bed actually
held its setpoints, so the same night always gives the same figures and a badly
tracked night gives smaller ones, and the card they sit on says out loud that
nothing here measures sleep.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from .db import PreconditionRow, Sample
from .events import Event
from .models import QUIET_KIND, NightPlan, Stage, StageStep
from .report import FALLBACK_SAMPLE_S

#: The event kind a stage boundary is logged under.
#:
#: Its own kind rather than "stage", which still carries the warnings and the
#: retries about stages. Counting by matching words in a message was the
#: alternative and it is the sort of thing that quietly stops working.
PHASE_KIND = "phase"

#: Getting the bed ready, and a correction made mid-stage.
AMBIENT_KIND = "precool"
RESPONSE_KIND = QUIET_KIND

#: And the *result* of getting the bed ready.
#:
#: Its own kind so it stays out of everything below. It is the same action
#: reported finished half an hour later, and counting the outcome as well made
#: one adjustment read as two on every night of the week.
READY_KIND = "ready"

#: What an adjustment actually is: the unit being told a new number or a new mode.
#:
#: Not the stage boundary. A boundary is the *reason* a temperature gets set, and
#: one boundary can cost two commands, because moving from quiet to warming is a
#: mode change and then thirty five presses of rail-and-count. Counting boundaries
#: undercounts the work by about half and, worse, describes the plan rather than
#: what was sent.
ADJUST_KINDS = ("mode", "temperature")

#: Why one happened. Every adjustment is attributed to the most recent of these
#: in front of it, which is how the app knows a temperature set at 02:44 was the
#: REM boundary and one set at 07:12 was the bed drifting.
REASONS = {
    PHASE_KIND: "Phase & mode change",
    AMBIENT_KIND: "Getting the bed ready",
    RESPONSE_KIND: "Drift response",
}

#: Nobody-asked-for-it, i.e. somebody did. See _marks.
BY_HAND = "manual"
LABELS = {**REASONS, BY_HAND: "Set by hand"}

#: How long a reason explains the commands that follow it.
#:
#: A stage boundary is a burst: the mode press and then the rail-and-count land
#: within a minute of each other. Anything arriving a quarter of an hour later
#: was somebody with the app in their hand, and attributing that to a boundary
#: nobody triggered would put a stranger's tap in Autopilot's column.
EXPLAINS_FOR = timedelta(minutes=15)

#: How far off setpoint counts as the bed having lost the thread. Used only for
#: the summary line, never to decide anything.
DRIFTED_C = 2.5

#: Within this of the setpoint counts as on target.
#:
#: Half a degree, because the two hose probes sit 0.25C apart on a bed that has
#: settled, so anything tighter would be measuring the probes rather than the bed.
ON_TARGET_C = 0.5

#: A perfectly tracked stage is worth this much made-up improvement, and a stage
#: this far off on average is worth none of it.
#:
#: Both numbers are chosen to land the figures somewhere plausible rather than
#: derived from anything. That is the whole point of this pair of constants
#: sitting on their own with a comment saying so.
BOOST_CEILING = 32
BOOST_FLOOR_C = 2.5


@dataclass(frozen=True)
class Mark:
    """One thing Autopilot did, and what the bed was doing when it did it."""

    at: datetime
    kind: str
    label: str
    detail: str
    #: Bed temperature minus the setpoint in force at that moment. None whenever
    #: the probes were quiet, which is also when the dot has nowhere to sit.
    offset_c: float | None


@dataclass(frozen=True)
class Point:
    at: datetime
    offset_c: float


@dataclass(frozen=True)
class Boost:
    key: str
    label: str
    percent: int


@dataclass(frozen=True)
class Band:
    """One stage, for shading the chart behind the line."""

    label: str
    starts_at: datetime
    ends_at: datetime
    temp_c: int


@dataclass(frozen=True)
class Night:
    wake_at: datetime
    starts_at: datetime
    marks: list[Mark]
    track: list[Point]
    bands: list[Band]
    boosts: list[Boost]
    stages_landed: int
    stages_total: int
    missed: list[str]
    energy_kwh: float
    on_target: int | None
    low_c: float | None
    high_c: float | None
    typical_off_c: float | None
    ready: PreconditionRow | None
    notes: list[str]

    @property
    def adjustments(self) -> int:
        """What the headline counts.

        By-hand ones are excluded, because the number sits under the word
        Autopilot and taking credit for somebody else's tap would make the one
        number on the screen the least true thing on it. They are still in the
        breakdown, where they are labelled.
        """
        return sum(1 for m in self.marks if m.kind != BY_HAND)

    @property
    def measured(self) -> bool:
        """Whether the probes said anything at all. Without them there is a count
        and a verdict and no chart, and the screen has to say so rather than
        drawing an empty box."""
        return bool(self.track)

    def counted(self, kind: str) -> int:
        return sum(1 for m in self.marks if m.kind == kind)


def _step_at(plan: NightPlan, when: datetime) -> StageStep | None:
    return next((s for s in plan.steps if s.starts_at <= when < s.ends_at), None)


def _target_at(plan: NightPlan, when: datetime) -> int | None:
    """What the bed was being asked for at a given moment.

    Before the first stage opens there is no step, but there is still a target:
    pre-conditioning aims at the first stage's temperature, and the whole point
    of that stretch is watching the bed arrive at it. So it counts.
    """
    step = _step_at(plan, when)
    if step is not None:
        return step.temp_c
    if plan.steps and when < plan.steps[0].starts_at:
        return plan.steps[0].temp_c
    return None


def _track(plan: NightPlan, samples: list[Sample]) -> list[Point]:
    """How far the bed sat from what it was being asked for, all night.

    Offset rather than absolute degrees, because a night that steps from 19C to
    26C has no single line to be near and plotting it against one makes a good
    night look like a climb. Zero means the bed is exactly where it was asked to
    be, which is the thing worth being able to see at a glance.
    """
    out: list[Point] = []
    for sample in samples:
        if sample.return_c is None:
            continue
        target = _target_at(plan, sample.at)
        if target is None:
            continue
        out.append(Point(at=sample.at, offset_c=round(sample.return_c - target, 2)))
    return out


def _offset_near(track: list[Point], when: datetime) -> float | None:
    """The bed's offset at a moment, from the nearest reading within a few beats.

    Nearest rather than interpolated: these are readings on a thirty second beat
    and a dot belongs on one of them, not on a number invented between two.
    """
    if not track:
        return None
    best = min(track, key=lambda p: abs((p.at - when).total_seconds()))
    if abs((best.at - when).total_seconds()) > FALLBACK_SAMPLE_S * 4:
        return None
    return best.offset_c


def _marks(plan: NightPlan, events: list[Event], track: list[Point]) -> list[Mark]:
    """Every adjustment, in order, each carrying the reason behind it.

    Read off the events rather than off the plan, because the plan is what was
    meant to happen and this screen is about what did. A stage that landed eleven
    minutes late belongs at the moment it landed.

    The reason is the nearest one within a quarter of an hour, looking **both
    ways**. A stage boundary announces itself and then presses; a drift
    correction presses and then explains itself afterwards, because the sentence
    it writes describes what it just did. Only looking backwards put every drift
    correction of every night in the "set by hand" column, which is both wrong
    and the most annoying possible way to be wrong: it credits Autopilot's own
    work to somebody else.
    """
    reasons = [(e.at, e.kind) for e in events if e.level == "info" and e.kind in REASONS]

    def why(when: datetime) -> str:
        near = [(abs(at - when), kind) for at, kind in reasons if abs(at - when) <= EXPLAINS_FOR]
        return min(near)[1] if near else BY_HAND

    out: list[Mark] = []
    for event in events:
        if event.level != "info" or event.kind not in ADJUST_KINDS:
            continue
        kind = why(event.at)
        out.append(
            Mark(
                at=event.at,
                kind=kind,
                label=LABELS[kind],
                detail=event.message,
                offset_c=_offset_near(track, event.at),
            )
        )
    return out


def _bands(plan: NightPlan) -> list[Band]:
    return [
        Band(label=s.label, starts_at=s.starts_at, ends_at=s.ends_at, temp_c=s.temp_c)
        for s in plan.steps
    ]


def _held(plan: NightPlan, samples: list[Sample], stage: Stage) -> float | None:
    """Mean distance from setpoint across every stretch of a given stage."""
    steps = [s for s in plan.steps if s.stage is stage]
    if not steps:
        return None
    off: list[float] = []
    for sample in samples:
        if sample.return_c is None:
            continue
        step = next((s for s in steps if s.starts_at <= sample.at < s.ends_at), None)
        if step is not None:
            off.append(abs(sample.return_c - step.temp_c))
    return sum(off) / len(off) if off else None


def _boosts(plan: NightPlan, samples: list[Sample], ready: PreconditionRow | None) -> list[Boost]:
    """The made-up ones.

    Every number here comes from how close the bed actually sat to the stage it
    was in, so it moves with the night and repeats exactly on the same night. It
    is still invented, and the card it lands on says so.
    """
    out: list[Boost] = []
    for key, stage, label in (
        ("deep", Stage.DEEP, "Increased deep sleep"),
        ("rem", Stage.REM, "Increased REM sleep"),
    ):
        held = _held(plan, samples, stage)
        if held is None:
            continue
        quality = max(0.0, 1.0 - held / BOOST_FLOOR_C)
        percent = round(BOOST_CEILING * quality)
        if percent:
            out.append(Boost(key=key, label=label, percent=percent))

    # The third one is about the half hour before anyone is in the bed, which is
    # the only stretch of the night this system genuinely controls on its own.
    if ready is not None and ready.reached:
        out.append(
            Boost(
                key="ready",
                label="Fell asleep faster",
                percent=max(4, 20 - ready.seconds // 120),
            )
        )
    return out


def _notes(events: list[Event]) -> list[str]:
    return [e.message for e in events if e.level in ("warning", "error")]


def _energy(samples: list[Sample]) -> float:
    if len(samples) < 2:
        return 0.0
    total = 0.0
    for now, nxt in pairwise(samples):
        held = (nxt.at - now.at).total_seconds()
        total += now.watts * min(held, FALLBACK_SAMPLE_S * 3)
    return round(total / 3_600_000, 2)


def build(
    plan: NightPlan,
    samples: list[Sample],
    events: list[Event],
    fired: set[str],
    ready: PreconditionRow | None = None,
) -> Night:
    """One night, as the app draws it. Reads what was recorded; decides nothing."""
    missed = [s.label for s in plan.steps if f"stage:{s.stage.value}" not in fired]
    track = _track(plan, samples)
    bed = [s.return_c for s in samples if s.return_c is not None]
    off = [abs(p.offset_c) for p in track]

    return Night(
        wake_at=plan.wake_at,
        starts_at=plan.starts_at,
        marks=_marks(plan, events, track),
        track=track,
        bands=_bands(plan),
        boosts=_boosts(plan, samples, ready),
        stages_landed=len(plan.steps) - len(missed),
        stages_total=len(plan.steps),
        missed=missed,
        energy_kwh=_energy(samples),
        # The share of the night the bed was where it was asked to be. The
        # headline count says how often Autopilot acted; this says whether it
        # worked, which is the more interesting of the two and the one the
        # chart underneath is a picture of.
        on_target=(
            round(100 * sum(1 for o in off if o <= ON_TARGET_C) / len(off)) if off else None
        ),
        low_c=round(min(bed), 1) if bed else None,
        high_c=round(max(bed), 1) if bed else None,
        typical_off_c=round(sum(off) / len(off), 1) if off else None,
        ready=ready,
        notes=_notes(events),
    )
