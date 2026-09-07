"""Reading ESPHome's capture log, and judging whether a capture is any good.

Setup-time only. Nothing in the running service imports this: it exists so that
`scripts/capture.py` can be a thin loop around logic that has tests, rather than
a few hundred lines of parsing that has never run before the evening it matters.

The hard part is not reading the lines. It is deciding whether two presses of the
same button really said the same thing, when the log will not repeat itself
exactly and one press arrives as three lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: ESPHome colours its log, and the escape codes sit in the middle of the text.
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: "[14:23:14][I][remote.nec:070]: Received NEC: address=0x10EF, command=0x54AB"
RECEIVED = re.compile(r"Received\s+([A-Za-z0-9_]+):\s*(.+)$")

#: Protocols that really mean "could not decode this, here are the timings".
#: A named protocol is worth far more: four bytes against a hundred numbers, and
#: self-correcting where a single mistyped timing is not.
UNDECODED = ("raw", "pronto")

#: Raw timings never repeat exactly, because the remote and the receiver both
#: jitter by tens of microseconds. Round to this before comparing two presses.
RAW_BUCKET_US = 100

#: How long after the first line of a press to keep collecting. `dump: all` runs
#: every decoder it has, so one press arrives as two or three lines at once.
PRESS_WINDOW_S = 0.45

#: The eight buttons, in the order the script asks for them, with what to look
#: for on the remote. The names are the entity ids the service expects.
BUTTONS: tuple[tuple[str, str], ...] = (
    ("power", "Power"),
    ("schedule", "Crescent moon"),
    ("temp_up", "Up arrow"),
    ("temp_down", "Down arrow"),
    ("cool", "Snowflake"),
    ("warm", "Sun"),
    ("timer", "Clock"),
    ("mute", "Speaker with X"),
)


@dataclass(frozen=True)
class Reading:
    """One decoded code, from one line of the log."""

    protocol: str
    payload: str

    @property
    def is_named(self) -> bool:
        """A real protocol, rather than a pile of timings."""
        return self.protocol not in UNDECODED

    def describe(self) -> str:
        body = self.payload if len(self.payload) <= 68 else self.payload[:65] + "..."
        return f"{self.protocol.upper()}  {body}"


def parse_line(line: str) -> Reading | None:
    """One log line, or None if it is not a captured code."""
    clean = ANSI.sub("", line).strip()
    match = RECEIVED.search(clean)
    if match is None:
        return None
    return Reading(match.group(1).strip().lower(), match.group(2).strip())


def best(readings: list[Reading]) -> Reading | None:
    """The one worth keeping out of a single press.

    `dump: all` describes the same press several ways. A named protocol beats raw
    timings every time, and raw beats pronto because raw is what the transmitter
    takes directly.
    """
    if not readings:
        return None
    named = [r for r in readings if r.is_named]
    if named:
        return named[0]
    raw = [r for r in readings if r.protocol == "raw"]
    return raw[0] if raw else readings[0]


def signature(reading: Reading) -> str:
    """What has to match for two presses to count as the same button.

    Not the whole payload. `command_repeats` counts how long the button was held
    down, so it differs between two presses of the same button and must not
    count. Raw timings are rounded first, for the same reason.
    """
    if reading.protocol in ("raw", "pronto"):
        return _loose_signature(reading)
    parts = [
        part.strip()
        for part in reading.payload.split(",")
        if part.strip() and not part.strip().startswith("command_repeats")
    ]
    return reading.protocol + ":" + ",".join(parts)


def _loose_signature(reading: Reading) -> str:
    values: list[int] = []
    for chunk in reading.payload.replace(",", " ").split():
        try:
            values.append(round(int(chunk) / RAW_BUCKET_US) * RAW_BUCKET_US)
        except ValueError:
            # Pronto is hex words, and anything else unparseable compares whole.
            return reading.protocol + ":verbatim:" + reading.payload
    return f"{reading.protocol}:{len(values)}:" + ",".join(str(v) for v in values)


def held_too_long(reading: Reading) -> bool:
    """The remote repeats the frame while a button is held. Harmless, but a
    shorter tap gives a cleaner capture."""
    match = re.search(r"command_repeats=(\d+)", reading.payload)
    return match is not None and int(match.group(1)) > 1


@dataclass
class Verdict:
    ok: bool
    headline: str
    detail: str = ""


def judge(presses: list[Reading], wanted: int) -> Verdict:
    """Whether a button's presses agree with each other."""
    if len(presses) < wanted:
        return Verdict(False, f"only {len(presses)} of {wanted} presses")

    signatures = {signature(p) for p in presses}
    if len(signatures) > 1:
        return Verdict(
            False,
            f"{len(signatures)} different codes from {len(presses)} presses",
            "Too close, too far, or off to one side. Square on at about 10cm.",
        )

    reading = best(presses)
    assert reading is not None
    if not reading.is_named:
        return Verdict(
            True,
            "consistent, raw timings only",
            "No standard protocol matched. Usable, just longer to write in.",
        )
    if any(held_too_long(p) for p in presses):
        return Verdict(True, "consistent", "One press was held a little long. Harmless.")
    return Verdict(True, "consistent")


@dataclass
class Capture:
    """Everything gathered for one button."""

    name: str
    label: str
    presses: list[Reading] = field(default_factory=list)

    @property
    def reading(self) -> Reading | None:
        return best(self.presses)


def cross_check(captures: list[Capture]) -> list[str]:
    """Problems only visible with all eight side by side.

    A bad capture usually looks perfectly fine on its own. What gives it away is
    sitting next to its siblings: one button decoding as a different protocol, or
    two buttons somehow producing the same code because one was pressed twice.
    """
    done = [c for c in captures if c.presses]
    if len(done) < 2:
        return []

    problems: list[str] = []

    protocols = {c.reading.protocol for c in done if c.reading}
    if len(protocols) > 1:
        problems.append(
            "Buttons decoded as different protocols ("
            + ", ".join(sorted(protocols))
            + "). One remote should speak one language, so at least one of these "
            "is probably a misread."
        )

    seen: dict[str, str] = {}
    for capture in done:
        if capture.reading is None:
            continue
        sig = signature(capture.reading)
        if sig in seen:
            problems.append(
                f"{seen[sig]} and {capture.name} captured the identical code. "
                "Almost certainly the same button pressed twice."
            )
        seen[sig] = capture.name

    addresses = set()
    for capture in done:
        if capture.reading is None:
            continue
        match = re.search(r"address=(\S+?)(?:,|$)", capture.reading.payload)
        if match:
            addresses.add(match.group(1))
    if len(addresses) > 1:
        problems.append(
            "More than one address across the eight ("
            + ", ".join(sorted(addresses))
            + "). Every button on one remote normally shares an address, so the "
            "odd one out is worth re-capturing."
        )

    return problems


def report(captures: list[Capture], wanted: int) -> str:
    """The summary to save and send on. Written to be read by a person."""
    lines = ["HydroSnooze infrared capture", ""]
    for capture in captures:
        verdict = judge(capture.presses, wanted)
        mark = "OK  " if verdict.ok else "BAD "
        lines.append(f"{mark}{capture.name:<11} ({capture.label})")
        if capture.presses:
            for index, press in enumerate(capture.presses, 1):
                lines.append(f"       press {index}: {press.protocol.upper()}  {press.payload}")
        else:
            lines.append("       nothing captured")
        lines.append(f"       {verdict.headline}")
        if verdict.detail:
            lines.append(f"       {verdict.detail}")
        lines.append("")

    problems = cross_check(captures)
    lines.append("Across all eight:")
    if problems:
        lines.extend(f"  - {problem}" for problem in problems)
    else:
        lines.append("  nothing inconsistent")
    lines.append("")
    return "\n".join(lines)
