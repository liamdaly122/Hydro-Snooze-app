"""Whether the clock can be trusted yet.

A Raspberry Pi has no battery-backed clock. It does not know the time when it
boots; it knows the time when something on the network tells it. In between,
systemd sets the clock to roughly when the machine last shut down, which is close
enough to look right and wrong enough to matter.

The scheduler works in plain local time and cannot tell. A Pi that has been
unplugged since Friday, plugged back in on Monday evening, starts up believing it
is Friday afternoon and schedules a night that ended three days ago. Then NTP
answers and the clock jumps three days forward in one step.

`After=network-online.target` in the unit file says the network is up. It does not
say the clock is right, and the two are not the same thing. So the service waits
here, and the unit file orders after `time-sync.target` as well, which is the
belt to this brace.

Nothing here is Pi-specific in a way that breaks a Mac. A machine that cannot
answer the question returns None and is trusted, which is the right answer for
every machine with a clock of its own.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

#: systemd-time-wait-sync touches this once the clock has been set from the
#: network. Checking for it costs nothing, so it is tried before the subprocess.
SYNCED_FLAG = "/run/systemd/timesync/synchronized"

#: How long to leave between asking again while the answer is still no. The tick
#: loop runs at 1 Hz and a subprocess a second to ask a question whose answer
#: changes once at boot would be silly.
ASK_EVERY_S = 5.0

#: How long to hold off scheduling while the clock is still unset, before giving
#: up and running anyway.
#:
#: There has to be a bound. NTP normally answers within seconds of the network
#: coming up, so ten minutes means something is wrong: no route out, a blocked
#: port, a router that has not finished booting either. Waiting forever would
#: turn a wrong clock into no night at all, which is the worse of the two. It
#: runs, and it says loudly that it is running on a clock nothing confirmed.
GIVE_UP_AFTER_S = 10 * 60

_answer: bool | None = None
_asked_at: float | None = None


def _ask() -> bool | None:
    """True, False, or None when this machine has no opinion."""
    import os

    if os.path.exists(SYNCED_FLAG):
        return True
    if shutil.which("timedatectl") is None:
        # No systemd, so no timesyncd, so nothing to wait for. A Mac lands here.
        return None
    try:
        done = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        # systemd present but not running the show, which happens inside
        # containers. Not an answer, so not a reason to hold anything up.
        return None
    said = done.stdout.strip().lower()
    if said == "yes":
        return True
    if said == "no":
        return False
    return None


def synchronised() -> bool | None:
    """Has the clock been set from the network?

    Sticky once true. Losing NTP later does not make the clock wrong, it makes it
    slowly drift, and the risk this exists for is entirely at boot.

    Uses the monotonic clock for its own throttling, which is the one clock in
    the process that a correction cannot move.
    """
    global _answer, _asked_at

    if _answer is True:
        return True
    now = time.monotonic()
    if _asked_at is not None and now - _asked_at < ASK_EVERY_S:
        return _answer
    _asked_at = now
    _answer = _ask()
    return _answer


def reset() -> None:
    """Forget the cached answer. For tests."""
    global _answer, _asked_at
    _answer = None
    _asked_at = None
