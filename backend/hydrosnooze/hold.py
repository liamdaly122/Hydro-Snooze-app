"""Holding the bed at the number: Stay on target, and the trim.

Two things decide how close the bed stays to what a part asks for.

**Stay on target.** Warming mode on this unit sounds like a geiger counter and
cooling is silent, and between 25 and 30C both can be set to the same number.
Each part starts in the mode its direction calls for: a part that steps the
temperature up warms, one that steps it down cools (models.mode_for_target).
The question is whether it stays there.

    Off   a warm part is handed to the quiet cooling mode once the bed reaches
          the number, and warms again once it has fallen a degree below. Quieter,
          and the bed swings about a degree under the number
    On    every part stays in the mode it started in, all night. No swaps, so
          the bed stays at the number, and warm parts are as loud as warming is.
          A nudge or a bedside press part way through moves it the way the
          number moved, and it stays in that (Service._stay_mode)

Off is what the Balanced level did, the default from 26 September. There used to
be three levels, Quiet, Balanced and Close, and testing them on the real bed
they did not change much (27 September). All three still swapped, and every
swap starts the trim's half hour again, so the trim rarely got a full window to
act on. Staying in one mode is what lets it finish the window and hold the
number. A database set to Close reads as on, being the level that already kept
warm parts warming; the other two read as off.

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

from datetime import datetime, timedelta

#: With Stay on target off, a warm part goes quiet once the bed is at least
#: target + QUIET_AT_C...
QUIET_AT_C = 0.0

#: ...and warms again once it is at or below target - WARM_AGAIN_C.
WARM_AGAIN_C = 1.0

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
