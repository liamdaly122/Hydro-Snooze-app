"""How a night felt, from me rather than from the mat.

The mat measures a great deal and none of it is how waking up felt, which is
the whole of what the Wake part of the night is for. The scoreboard said so and
left Wake unscored. A one-tap answer in the morning fills that gap: how I woke
up, from Rough to Great, and whether the bed felt too cold, right or too warm.

**Tags are for what the bed did not do.** A night after a few drinks loses REM
whatever the temperature, and a night with somebody else in the bed is two
bodies' worth of heat on a pad set for one. The scoreboard compares settings a
degree apart, and nights like those would move its averages by more than the
degree does. Three tags leave a night out of the scoreboard altogether, the
same way a night changed by hand already is: Alcohol, Ill, and Someone else in
the bed. The rest are recorded and shown, and change nothing.

A fixed list rather than tags anyone can make up, so that each one means the
same thing on every night it is on, and the three that matter cannot be
misspelt into not mattering.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How waking up felt, one to five.
RATINGS: dict[int, str] = {1: "Rough", 2: "Groggy", 3: "OK", 4: "Good", 5: "Great"}

#: How the bed felt, over the night as a whole.
FELT: dict[str, str] = {"too_cold": "Too cold", "right": "Right", "too_warm": "Too warm"}


@dataclass(frozen=True)
class Tag:
    key: str
    label: str
    #: A night with this on it is left out of the scoreboard.
    leaves_out: bool


TAGS: tuple[Tag, ...] = (
    Tag("alcohol", "Alcohol", True),
    Tag("ill", "Ill", True),
    Tag("company", "Someone else in the bed", True),
    Tag("caffeine", "Late caffeine", False),
    Tag("late_meal", "Late meal", False),
    Tag("exercise", "Exercise", False),
    Tag("stressed", "Stressed", False),
)

BY_KEY: dict[str, Tag] = {t.key: t for t in TAGS}


@dataclass(frozen=True)
class NightNote:
    """One morning's answer. Everything optional: a tag with no rating is fine."""

    wake_on: str
    rating: int | None = None
    felt: str | None = None
    tags: tuple[str, ...] = ()

    def left_out_by(self) -> list[str]:
        """The labels of the tags that leave this night out, in list order."""
        return [t.label for t in TAGS if t.leaves_out and t.key in self.tags]


def note_json(note: NightNote | None, wake_on: str) -> dict[str, object]:
    """One morning's note as the app draws it, with the choices it offers."""
    note = note or NightNote(wake_on)
    return {
        "wake_on": note.wake_on,
        "rating": note.rating,
        "felt": note.felt,
        "tags": list(note.tags),
        "left_out": note.left_out_by(),
        "choices": {
            "ratings": [{"value": k, "label": v} for k, v in RATINGS.items()],
            "felt": [{"value": k, "label": v} for k, v in FELT.items()],
            "tags": [{"key": t.key, "label": t.label, "leaves_out": t.leaves_out} for t in TAGS],
        },
    }
