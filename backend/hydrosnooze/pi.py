"""What the Pi says about its own power and heat.

Under-voltage is the most common reason a Raspberry Pi behaves as though it has a
software bug. A supply that sags under load does not announce itself: the machine
stays up, the network stutters, reads fail, the SD card corrupts eventually, and
every symptom points somewhere else. The setup notes have warned about it from
the start, and warning about it is not the same as noticing it.

The Pi does know. It keeps a bitmask of what its power and thermal management has
had to do, both right now and at any point since boot, and `vcgencmd get_throttled`
reads it out. So the question the notes could only raise, the machine can answer.

Nothing here is required. Anything that is not a Pi returns None and is left
alone.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

#: The bits, from the Raspberry Pi documentation. The low four are the state
#: right now; the ones at 16 and up are sticky and say it has happened at least
#: once since boot.
NOW = {
    0: "not getting enough power",
    1: "having its processor clocked down",
    2: "being throttled",
    3: "over its temperature limit",
}
SINCE_BOOT = {
    16: "has not been getting enough power",
    17: "has had its processor clocked down",
    18: "has been throttled",
    19: "has been over its temperature limit",
}

#: The one that means a supply or a cable, rather than a warm room. It is also
#: the one that quietly ruins SD cards, so it is worth naming separately.
UNDERVOLTAGE = (1 << 0) | (1 << 16)


def throttled() -> int | None:
    """The mask, or None on anything that is not a Pi."""
    if shutil.which("vcgencmd") is None:
        return None
    try:
        done = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    found = re.search(r"throttled=0x([0-9a-fA-F]+)", done.stdout)
    if found is None:
        return None
    return int(found.group(1), 16)


def describe(mask: int) -> str:
    """Said in a way that names the likely cause, not the bit that was set.

    "throttled=0x50005" is a fact about a register. What is wanted at 7am is
    which cable to change.
    """
    now = [text for bit, text in NOW.items() if mask & (1 << bit)]
    before = [text for bit, text in SINCE_BOOT.items() if mask & (1 << bit)]

    said = []
    if now:
        said.append("The Pi is " + " and ".join(now) + " right now.")
    elif before:
        said.append("The Pi " + " and ".join(before) + " since it was switched on.")

    if mask & UNDERVOLTAGE:
        said.append(
            "That is almost always the power supply or the cable rather than "
            "anything running on it. Use the official Pi supply, and not a phone "
            "charger. Left alone it corrupts the SD card eventually."
        )
    elif mask:
        said.append("Somewhere warmer than it wants to be, or short of airflow.")
    return " ".join(said)
