"""The evening suggestion: tonight's Deep and REM, chosen from the scoreboard.

Step two of a cleverer Autopilot. Each evening, before the night starts, this
picks tonight's temperatures for Deep and REM and the app offers them: use them
for tonight, or not. Nothing changes unless that is tapped, and taking one only
ever changes tonight. Step three will be the same choice made without asking.

**What it pushes for:** deep sleep and REM together, which is what the
scoreboard scores both parts on, as long as time awake and time to fall asleep
do not get worse.

**Most nights it runs the best so far.** Best means the scoreboard's leader, but
only once the scoreboard has called it clearly ahead, only inside the limits,
and only if it does not cost GUARD_S more time awake or falling asleep than the
usual. Until something is clearly better, the best so far is the usual: the
schedule's own temperature for that part.

**About one night in TEST_EVERY is a test:** one part, one degree either side of
the best, never outside the limits. It picks the part with fewer test nights so
far and the side with fewer nights at it, so the gaps in the scoreboard fill in
evenly. The dice are seeded with the night's date, so opening the app twice in
one evening shows the same suggestion.

**Limits:** each part stays within `reach` degrees of where it was when the
limits were set, REACH_DEFAULT to begin with and REACH_MAX at most. They do not
follow the schedule about: move the usual Deep and the limits stay where they
were until they are set again, so a run of suggestions can never walk the bed
somewhere nobody chose.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

#: Degrees either side of the usual a part may go. Two to start, three at most.
REACH_DEFAULT = 2
REACH_MAX = 3

#: About one night in this many is a test.
TEST_EVERY = 3

#: The best so far may not cost more than this in time awake or time to fall
#: asleep, against the usual. Ten minutes, well inside what one night swings by
#: and well outside what a person would shrug off every night.
GUARD_S = 10 * 60

#: The parts it moves. Drift and Wake stay yours: one is about falling asleep,
#: the other about waking, and neither is what deep sleep and REM measure.
PARTS = ("deep", "rem")

LABEL = {"deep": "Deep", "rem": "REM"}


@dataclass(frozen=True)
class Limit:
    centre_c: int
    reach: int

    @property
    def low_c(self) -> int:
        return self.centre_c - self.reach

    @property
    def high_c(self) -> int:
        return self.centre_c + self.reach

    def holds(self, c: int) -> bool:
        return self.low_c <= c <= self.high_c


@dataclass(frozen=True)
class Choice:
    wake_on: str
    usual: dict[str, int]
    best: dict[str, int]
    tonight: dict[str, int]
    test_part: str | None
    test_offset_c: int | None
    why: str

    @property
    def changes_anything(self) -> bool:
        return self.tonight != self.usual


def choose(
    board: dict[str, Any],
    usual: dict[str, int],
    limits: dict[str, Limit],
    wake_on: str,
    *,
    lowest: int,
    highest: int,
) -> Choice:
    """Tonight's Deep and REM. `board` is scoreboard.scoreboard's answer;
    `lowest` and `highest` are what the unit can express under the safety cap."""
    parts = {p["part"]: p for p in board["parts"]}
    allowed = {
        part: (lambda c, lim=limits[part]: lim.holds(c) and lowest <= c <= highest)
        for part in PARTS
    }

    best: dict[str, int] = {}
    why: list[str] = []
    for part in PARTS:
        pick, reason = _best(parts.get(part), usual[part], allowed[part], LABEL[part])
        best[part] = pick
        if reason:
            why.append(reason)

    rng = random.Random(f"hydrosnooze:suggest:{wake_on}")
    test_part, offset = None, None
    if rng.random() < 1 / TEST_EVERY:
        tests = {
            part: sum(s["tests"] for s in (parts.get(part) or {}).get("settings", []))
            for part in PARTS
        }
        for part in sorted(PARTS, key=lambda p: (tests[p], rng.random())):
            sides = [o for o in (-1, 1) if allowed[part](best[part] + o)]
            if not sides:
                continue
            nights = {s["set_c"]: s["nights"] for s in (parts.get(part) or {}).get("settings", [])}
            offset = min(sides, key=lambda o: (nights.get(best[part] + o, 0), rng.random()))
            test_part = part
            break

    tonight = dict(best)
    if test_part is not None and offset is not None:
        tonight[test_part] = best[test_part] + offset
        which = "cooler" if offset < 0 else "warmer"
        why.insert(
            0,
            f"A test night: {LABEL[test_part]} a degree {which} than the best so far, "
            "to see what it does to your deep sleep and REM.",
        )
    if not why:
        why.append("Nothing is clearly better than your usual yet, so tonight runs your usual.")

    return Choice(
        wake_on=wake_on,
        usual=dict(usual),
        best=best,
        tonight=tonight,
        test_part=test_part,
        test_offset_c=offset,
        why=" ".join(why),
    )


def _best(
    part: dict[str, Any] | None, usual_c: int, allowed, label: str
) -> tuple[int, str | None]:
    """The best so far for one part, and why, when it is not the usual."""
    if not allowed(usual_c):
        # The usual itself moved outside the limits since they were set. Stay
        # as near it as they allow rather than suggest what nobody chose.
        near = min((c for c in range(usual_c - 10, usual_c + 11) if allowed(c)),
                   key=lambda c: abs(c - usual_c), default=usual_c)
        # Said, because it is a change. With no reason given, the card offered
        # a different temperature under "tonight runs your usual".
        if near == usual_c:
            return near, None
        return near, (
            f"{label} at {near}°, as near your usual {usual_c}° as the limits allow. "
            "Pick How far it may go again to centre them on your usual."
        )
    if part is None or part["verdict"] != "clear" or part["leader_c"] is None:
        return usual_c, None
    leader = part["leader_c"]
    if leader == usual_c or not allowed(leader):
        return usual_c, None

    settings = {s["set_c"]: s for s in part["settings"]}
    lead = settings.get(leader)
    base = settings.get(usual_c) or settings.get(part["runner_c"])
    if lead and base and (_worse(lead["awake_s"], base["awake_s"])
                          or _worse(lead["asleep_after_s"], base["asleep_after_s"])):
        return usual_c, None
    return leader, (
        f"{part['label']} at {leader}°, which is clearly ahead of {part['runner_c']}° "
        "on your scoreboard."
    )


def _worse(now: int | None, before: int | None) -> bool:
    return now is not None and before is not None and now - before > GUARD_S
