"""The real power monitor, over the Shelly Gen3 local HTTP RPC.

Reads `Switch.GetStatus` and returns the `apower` field, which is the instantaneous
draw in watts. Everything is local: no Shelly cloud account, no internet.

A plug that cannot be reached returns None rather than zero. That distinction
matters, because zero watts means the unit is off and None means we do not know,
and the app is built to say "unknown" instead of guessing.

Every read is tried twice before giving up. See read_watts.

**Named, not numbered.** HS_SHELLY_HOST can be the plug's address or its name
(`shellypluspluguk-<id>.local`). The name is the one to use. The router hands the
plug a new address whenever it likes, and the plug had three in its first month:
.194, then .192, then .193. Each time it moved, the address in .env pointed at
nothing, and the night ran blind until somebody noticed.

A name is looked up once and the address it gives is kept, so an ordinary read
never waits on a lookup. Only when the plug stops answering there is the name
asked again, and if it now points somewhere else the read is tried there before
anything is called unreachable. A lookup that fails changes nothing: the last
address that worked is kept.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: How long to wait before the one retry. Short, because the caller is usually a
#: command sequence waiting to find out whether the unit came on.
RETRY_GAP_S = 0.5

#: How long a lookup of the plug's name may take. A .local name is answered by
#: the plug itself over the Wi-Fi, so it is either quick or not coming.
LOOKUP_TIMEOUT_S = 3.0

Lookup = Callable[[str], Awaitable[str | None]]


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


async def look_up(name: str) -> str | None:
    """The address a name has now, or None if nothing answered for it."""
    loop = asyncio.get_running_loop()
    try:
        found = await asyncio.wait_for(
            loop.getaddrinfo(name, 80, family=socket.AF_INET, type=socket.SOCK_STREAM),
            LOOKUP_TIMEOUT_S,
        )
    except (TimeoutError, OSError):
        return None
    return str(found[0][4][0]) if found else None


class _Silent(Exception):
    """Both tries at one address got nothing back."""


class ShellyPowerMonitor:
    def __init__(
        self,
        host: str,
        *,
        timeout: float = 4.0,
        switch_id: int = 0,
        client: httpx.AsyncClient | None = None,
        lookup: Lookup | None = None,
    ) -> None:
        self.host = host
        self.switch_id = switch_id
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._lookup = lookup or look_up
        #: The plug's name, when it was given one. None for an address, which
        #: is read as it is and never looked up.
        self._name = None if _is_address(host) else host
        #: Where reads go: the address as configured, or the last one the name
        #: gave. None until a name has been looked up for the first time.
        self.address: str | None = None if self._name else host

    async def read_watts(self) -> float | None:
        """The instantaneous draw, or None if the plug could not be reached.

        Tried twice. The plug sits behind a bed next to a metal chassis, which is
        a poor spot for 2.4GHz, and measured at -87 dBm here: about one read in
        twenty comes back empty. One dropped packet should not become a failed
        power command, because the caller treats an unreachable plug as a reason
        to stop rather than a reason to guess.

        Twice, not a loop. If the plug is genuinely gone, saying so within a
        second beats blocking a button sequence while it hopes. The one exception
        is a named plug that has moved: then it is asked once more, at the
        address its name gives now.
        """
        address = self.address or await self._find()
        if address is None:
            log.warning("Shelly %s could not be found on the network", self.host)
            return None
        try:
            payload = await self._ask_twice(address)
        except _Silent as silent:
            moved_to = await self._find() if self._name else None
            if moved_to is None or moved_to == address:
                # %r not %s: a failed connection can arrive with a message that
                # is an empty string, which logs as a colon and nothing.
                log.warning("Shelly at %s unreachable, twice: %r", address, silent.__cause__)
                return None
            log.warning("Shelly %s has moved from %s to %s", self._name, address, moved_to)
            try:
                payload = await self._ask(moved_to)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Shelly at %s unreachable as well: %r", moved_to, exc)
                return None

        watts = payload.get("apower")
        if watts is None:
            log.warning("Shelly at %s returned no apower field: %s", self.address, payload)
            return None
        return float(watts)

    async def _ask_twice(self, address: str) -> dict[str, Any]:
        try:
            return await self._ask(address)
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(RETRY_GAP_S)
        try:
            return await self._ask(address)
        except (httpx.HTTPError, ValueError) as exc:
            raise _Silent from exc

    async def _ask(self, address: str) -> dict[str, Any]:
        response = await self._client.get(
            f"http://{address}/rpc/Switch.GetStatus", params={"id": self.switch_id}
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _find(self) -> str | None:
        """Look the name up again. Only a found address replaces the old one."""
        assert self._name is not None
        found = await self._lookup(self._name)
        if found is not None:
            self.address = found
        return found

    async def reachable(self) -> bool:
        return await self.read_watts() is not None

    async def close(self) -> None:
        await self._client.aclose()
