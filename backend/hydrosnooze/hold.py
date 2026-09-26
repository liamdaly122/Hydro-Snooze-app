"""Holding the bed at the number: how quietly, and the trim.

Two things decide how close the bed stays to what a part asks for.

**The Hold level.** Warming mode on this unit sounds like a geiger counter and
cooling is silent, and between 25 and 35C both can be set to the same number. So
a warm part is handed to the quiet cooling mode once the bed has arrived, and
body heat holds it; warming comes back only once the bed has genuinely fallen
away. Where "arrived" and "fallen" sit is the trade between silence and
accuracy, and it is a setting because it is a judgement, not a fact:

    Quiet     quiet at 0.5 below the target, warming again at 2 below
    Balanced  quiet once at the target, warming again at 1 below
    Close     warming holds the target; quiet only a degree over it,
              warming again at 0.5 below

Quiet was the only behaviour until 26 September, and a 32C REM part spent the
small hours at 30: warming to 31.5, quiet, a slow fall to 30 in a cold room,
warming again. On target a quarter of the night. Balanced is the default now.

**The trim.** What the bed settles at in a mode is learned before bedtime, with
nobody in it, and applied once at the start of each part. At three in the
morning the room is colder, there is a body in the bed, and the hoses lose a
different amount of heat. Nothing checked. So now, once the bed has sat on one
side of the target by more than TRIM_BAND_C for a whole TRIM_WINDOW, and is not
still heading towards it, the setting sent moves a degree the other way. Then
another full window before it may move again, never more than CORRECTION_LIMIT_C
from the target counting the learned correction, and never past the safety cap.
It starts again at every part, and it is part of Autopilot: off, nothing trims.

A trim is one temperature command, about thirty five presses of infrared, so a
window of half an hour keeps it to a handful a night at most.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Hold:
    name: str
    label: str
    #: A warm part goes quiet once the bed is at least target + arrived_c.
    arrived_c: float
    #: And warms again once the bed is at or below target - fallen_c.
    fallen_c: float
    describe: str


HOLDS: dict[str, Hold] = {
    "quiet": Hold(
        "quiet",
        "Quiet",
        -0.5,
        2.0,
        "Quietest. The bed goes quiet half a degree short of a warm target and warms "
        "again at 2° below, so it can sit up to 2° under.",
    ),
    "balanced": Hold(
        "balanced",
        "Balanced",
        0.0,
        1.0,
        "Quiet once the bed reaches the target, warming again at 1° below. Within about "
        "a degree, with more warming time.",
    ),
    "close": Hold(
        "close",
        "Close",
        1.0,
        0.5,
        "Warm parts keep warming unless your body heat pushes the bed a degree over. "
        "Closest to the target, and the noisiest.",
    ),
}

DEFAULT_HOLD = "balanced"

#: What a trim is logged as. Its own kind, so the Autopilot screen counts it as
#: holding the number rather than as a mode swap, and so does the morning report.
TRIM_KIND = "trim"

#: And the one line a part gets when the trim has gone as far as it may. Not a
#: reason for any command, so kept apart from TRIM_KIND.
TRIM_LIMIT_KIND = "trim_limit"

#: How long the bed has to sit off the target before the trim moves, and so the
#: least time between one trim and the next.
TRIM_WINDOW = timedelta(minutes=30)

#: How far off counts. The two hose probes sit a quarter of a degree apart on a
#: settled bed, so anything tighter would be trimming the probes.
TRIM_BAND_C = 0.5

#: Still heading towards the target by at least this over the last half of the
#: window, and it is left to get there.
TRIM_STILL_C = 0.3


def trim_needed(readings: list[tuple[datetime, float]], target_c: int, now: datetime) -> int:
    """+1 to send a degree more, -1 a degree less, 0 to leave it.

    `readings` are (when, bed) since the part, the mode or the last trim
    changed, oldest first. Only a full window counts: readings reaching back at
    least TRIM_WINDOW, every one of them off the same side of the target by more
    than TRIM_BAND_C, and the bed not still closing on it.
    """
    if not readings or now - readings[0][0] < TRIM_WINDOW:
        return 0
    window = [(at, c) for at, c in readings if now - at <= TRIM_WINDOW]
    recent = [c for at, c in window if now - at <= TRIM_WINDOW / 2]
    if len(window) < 2 or len(recent) < 2:
        return 0
    values = [c for _, c in window]
    moved = recent[-1] - recent[0]
    if all(c < target_c - TRIM_BAND_C for c in values) and moved < TRIM_STILL_C:
        return 1
    if all(c > target_c + TRIM_BAND_C for c in values) and -moved < TRIM_STILL_C:
        return -1
    return 0
