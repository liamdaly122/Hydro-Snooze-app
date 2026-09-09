"""Telling systemd we are still alive, and shutting up when we are not.

`Restart=always` catches a process that dies. It does not catch one that is
running perfectly and doing nothing useful, which is the failure that actually
happened: the scheduler loop spun at 1Hz for four hours getting nowhere, and from
the outside the service looked healthy the whole time.

So the liveness signal is not "the process exists". It is "the scheduler ticked
recently". If ticks stop, the pings stop, and systemd restarts us.

Pure stdlib. sd_notify is a datagram to a socket path in the environment, so
there is nothing to install, and off a Pi there is no socket and every call here
becomes a no-op.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(**fields: str) -> bool:
    """Send one sd_notify message. False when not running under systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    # A leading @ means an abstract socket, written as a leading NUL.
    if address.startswith("@"):
        address = "\0" + address[1:]
    payload = "\n".join(f"{k}={v}" for k, v in fields.items()).encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(payload)
        return True
    except OSError as exc:
        log.debug("sd_notify failed: %r", exc)
        return False


def ready() -> bool:
    """Startup finished. Type=notify units are not considered up until this."""
    return notify(READY="1")


def interval_seconds() -> float | None:
    """How often systemd expects a ping, or None if it is not watching.

    WatchdogSec is passed as WATCHDOG_USEC. Pinging at half that leaves room for
    one to be late without being killed for it.
    """
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return None
    try:
        usec = int(raw)
    except ValueError:
        return None
    return max(usec / 2_000_000, 1.0) if usec > 0 else None


def alive() -> bool:
    return notify(WATCHDOG="1")
